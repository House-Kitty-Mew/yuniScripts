"""
ah_logger.py — Structured logging system for the Auction House.

Provides:
  - JSONL logging (ai_simulations, transactions, market_events)
  - Plaintext operational logging (auctionhouse.log)
  - Log rotation (by file size and age)
  - UUID-based log entry IDs for traceability
  - Verbose mode for later machine-readability

Usage:
    from AUCTIONHOUSE.ah_logger import get_logger
    log = get_logger()
    log.info("system", "Database initialized")
    log.transaction("list", {"listing_uuid": "...", "seller": "Steve"})
    log.ai_simulation(prompt, response, duration_ms=1234)
    log.market_event("start", {"event_name": "extreme_winter", "goal": 5000})
"""

import json, os, uuid, threading, time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

LOG_DIR = Path(__file__).parent / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

PLAINTEXT_LOG = LOG_DIR / "auctionhouse.log"
AI_LOG = LOG_DIR / "ai_simulations.jsonl"
EVENTS_LOG = LOG_DIR / "market_events.jsonl"

_MAX_LOG_BYTES = 5 * 1024 * 1024  # 5 MB per log file before rotation
_RETENTION_DAYS = 30


class AuctionHouseLogger:
    """Structured logger for the Auction House system."""

    def __init__(self, verbose: bool = True):
        self._verbose = verbose
        self._lock = threading.Lock()
        self._log_count = 0

    # ── Plaintext log ─────────────────────────────────────────────

    def _plaintext(self, level: str, message: str):
        """Write a line to the plaintext operational log."""
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{ts}] [{level}] {message}\n"
        self._rotate_if_needed(PLAINTEXT_LOG)
        try:
            with self._lock:
                with open(PLAINTEXT_LOG, "a") as f:
                    f.write(line)
        except OSError:
            pass  # Don't crash the AH if logging fails
        if self._verbose:
            try:
                print(f"[AH:{level}] {message}", flush=True)
            except OSError:
                # Windows + pipe: print(flush=True) throws OSError [Errno 22]
                # when stdout is a pipe. Logging went to file already — safe to ignore.
                pass

    # ── JSONL helpers ─────────────────────────────────────────────

    def _write_jsonl(self, path: Path, entry: dict):
        """Append a JSON entry to a JSONL file, rotating if needed."""
        self._rotate_if_needed(path)
        entry["_log_id"] = str(uuid.uuid4())
        entry["_timestamp_iso"] = datetime.now(timezone.utc).isoformat()
        try:
            with self._lock:
                with open(path, "a") as f:
                    f.write(json.dumps(entry, default=str) + "\n")
        except OSError:
            pass

    def _rotate_if_needed(self, path: Path):
        """Rotate log file if it exceeds max bytes."""
        try:
            if path.exists() and path.stat().st_size > _MAX_LOG_BYTES:
                rotated = path.with_suffix(f".{int(time.time())}.log")
                path.rename(rotated)
                # Clean old rotated files
                self._clean_old_logs()
        except OSError:
            pass

    def _clean_old_logs(self):
        """Remove log files older than retention period."""
        cutoff = time.time() - (_RETENTION_DAYS * 86400)
        try:
            for f in LOG_DIR.iterdir():
                if f.suffix == ".log" and f.stat().st_mtime < cutoff:
                    f.unlink(missing_ok=True)
        except OSError:
            pass

    # ── Public API ────────────────────────────────────────────────

    def info(self, source: str, message: str, data: Optional[dict] = None):
        """Log an informational message to the plaintext log.

        Args:
            source: Component name (e.g. 'database', 'core', 'ai_engine')
            message: Human-readable description
            data: Optional extra structured data (appended to plaintext line)
        """
        extra = f" | {json.dumps(data, default=str)}" if data else ""
        self._plaintext("INFO", f"[{source}] {message}{extra}")

    def warn(self, source: str, message: str, data: Optional[dict] = None):
        """Log a warning."""
        extra = f" | {json.dumps(data, default=str)}" if data else ""
        self._plaintext("WARN", f"[{source}] {message}{extra}")

    def error(self, source: str, message: str, data: Optional[dict] = None):
        """Log an error."""
        extra = f" | {json.dumps(data, default=str)}" if data else ""
        self._plaintext("ERROR", f"[{source}] {message}{extra}")

    def debug(self, source: str, message: str, data: Optional[dict] = None):
        """Log a debug message (only if verbose)."""
        if self._verbose:
            extra = f" | {json.dumps(data, default=str)}" if data else ""
            self._plaintext("DEBUG", f"[{source}] {message}{extra}")

    def transaction(self, tx_type: str, details: dict):
        """Log a transaction to the daily transaction JSONL file.

        Args:
            tx_type: 'list', 'bid', 'buy', 'cancel', 'expire', 'ai_sim_list', 'ai_sim_sold'
            details: Dict with transaction details (listing_uuid, actor, item, price, etc.)
        """
        today = datetime.now().strftime("%Y-%m-%d")
        tx_path = LOG_DIR / f"transactions_{today}.jsonl"
        entry = {
            "type": "transaction",
            "subtype": tx_type,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "details": details,
        }
        self._write_jsonl(tx_path, entry)
        self._plaintext("TX", f"{tx_type}: {json.dumps(details, default=str)[:200]}")

    def ai_simulation(self, prompt: str, response: str, duration_ms: float,
                      metadata: Optional[dict] = None):
        """Log a full AI simulation cycle (prompt + response) to the AI JSONL file.

        WARNING: The prompt and response can be large (several KB each). This is
        intentional — the AI simulation logs are the ground truth for debugging
        and market analysis.

        Args:
            prompt: The full prompt sent to DeepSeek
            response: The full response received from DeepSeek
            duration_ms: How long the API call took
            metadata: Optional dict with extra context (token counts, cycle number, etc.)
        """
        # Truncate prompt/response to a reasonable preview for the log line,
        # but keep the full text in the JSONL entry.
        preview_prompt = prompt[:200] + "..." if len(prompt) > 200 else prompt
        preview_response = response[:200] + "..." if len(response) > 200 else response

        entry = {
            "type": "simulation_cycle",
            "subtype": "ai_run",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "duration_ms": duration_ms,
            "prompt_preview": preview_prompt,
            "response_preview": preview_response,
            "prompt_full": prompt,
            "response_full": response,
            "metadata": metadata or {},
        }
        self._write_jsonl(AI_LOG, entry)
        self._plaintext("AI", f"Simulation cycle complete ({duration_ms:.0f}ms)")

    def market_event(self, action: str, details: dict):
        """Log a market event activation or resolution.

        Args:
            action: 'start', 'end', 'progress', 'escalate'
            details: Dict with event_name, title, affected_items, etc.
        """
        entry = {
            "type": "market_event",
            "subtype": action,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "details": details,
        }
        self._write_jsonl(EVENTS_LOG, entry)
        name = details.get("event_name", details.get("title", "unknown"))
        self._plaintext("EVENT", f"{action}: {name}")


# ── Global singleton ─────────────────────────────────────────────────
_instances: dict = {}
_instance_lock = threading.Lock()


def get_logger(verbose: Optional[bool] = None) -> AuctionHouseLogger:
    """Return the global AuctionHouseLogger singleton.

    Args:
        verbose: Override verbose setting. If None, reads from config on first call.
    """
    global _instances
    key = "default"
    if key not in _instances:
        with _instance_lock:
            if key not in _instances:
                if verbose is None:
                    # Try to read from config without circular import dependency
                    verbose = True  # sensible default
                _instances[key] = AuctionHouseLogger(verbose=verbose)
    return _instances[key]

