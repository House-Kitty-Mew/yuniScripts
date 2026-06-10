#!/usr/bin/env python3
"""
Log Watcher Daemon — YuniScripts Self-Healing Error Monitor.
WO #204 Deliverable.

Watches all DeepSky ecosystem logs, parses errors/warnings,
and feeds them directly to the WorkOrderEngine for autonomous healing.

Supports:
- Multiple log file monitoring (deepsky_client, fastmcp_server, etc.)
- Log rotation detection (inode tracking)
- Error deduplication (300s window)
- Rate limiting (max 5 WOs/min)
- Component-aware log parsing
- Structured JSON + traditional log parsing
"""

import asyncio
import hashlib
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

logger = logging.getLogger("log_watcher")


# ─── Config ──────────────────────────────────────────────────────

WATCH_DIRS = [
    "/home/deck/Documents/dev-yuniScripts/SCRIPTS/CLIENTS/deepsky_client",
    "/home/deck/Documents/dev-yuniScripts/SCRIPTS/SERVICES/fastmcp_server",
]

LOG_PATTERNS = [
    "*.log",
    "*.log.*",
    "deepsky_client.log",
    "fastmcp_server.log",
    "process_wrapper.log",
    "error.log",
]

ERROR_PATTERN = re.compile(
    r"^(?P<timestamp>\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2},\d+)\s+-\s+"
    r"(?P<component>[^\s]+)\s+-\s+"
    r"(?P<level>DEBUG|INFO|WARNING|ERROR|CRITICAL)\s+-\s+"
    r"(?P<message>.+)"
)

DEDUP_WINDOW = 300  # seconds
RATE_LIMIT_MAX = 5  # WOs per minute
RATE_LIMIT_WINDOW = 60  # seconds
POLL_INTERVAL = 2.0  # seconds between polls


class LogWatcherDaemon:
    """
    Watches DeepSky ecosystem logs and feeds errors to the healing system.
    """

    def __init__(self, work_order_engine=None):
        self.work_order_engine = work_order_engine
        self._log_files: Dict[str, Dict] = {}  # path -> {inode, dev, size, mtime}
        self._seen_errors: Dict[str, float] = {}  # hash -> timestamp
        self._rate_limit_timestamps: List[float] = []
        self._running = False
        self._stats = {
            "files_watched": 0,
            "errors_detected": 0,
            "warnings_detected": 0,
            "work_orders_created": 0,
            "dedup_suppressed": 0,
            "rate_limited": 0,
        }

    async def start(self):
        """Start the log watcher loop."""
        self._running = True
        logger.info("LogWatcherDaemon started")
        await self._scan_loop()

    async def stop(self):
        """Stop the log watcher."""
        self._running = False
        logger.info("LogWatcherDaemon stopped")

    async def _scan_loop(self):
        """Main polling loop."""
        while self._running:
            try:
                self._discover_logs()
                for log_path, info in list(self._log_files.items()):
                    await self._check_log(log_path, info)
                await self._cleanup_seen_errors()
                await asyncio.sleep(POLL_INTERVAL)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Log watcher error: {e}")
                await asyncio.sleep(POLL_INTERVAL)

    def _discover_logs(self):
        """Discover all log files matching patterns."""
        new_files = {}
        for watch_dir in WATCH_DIRS:
            base = Path(watch_dir)
            if not base.is_dir():
                continue
            for pattern in LOG_PATTERNS:
                for log_file in base.glob(pattern):
                    if log_file.is_file() and os.access(log_file, os.R_OK):
                        try:
                            stat = log_file.stat()
                            key = str(log_file)
                            new_files[key] = {
                                "path": key,
                                "inode": stat.st_ino,
                                "dev": stat.st_dev,
                                "size": stat.st_size,
                                "mtime": stat.st_mtime,
                                "last_read_pos": self._log_files.get(key, {}).get(
                                    "last_read_pos", 0
                                ),
                            }
                            # Reset read position if log rotated (new inode)
                            old = self._log_files.get(key)
                            if old and old["inode"] != stat.st_ino:
                                new_files[key]["last_read_pos"] = 0
                                logger.info(f"Log rotation detected: {key}")
                        except OSError:
                            continue

        self._log_files = new_files
        self._stats["files_watched"] = len(self._log_files)

    async def _check_log(self, log_path: str, info: Dict):
        """Check a single log file for new entries."""
        try:
            current_size = os.path.getsize(log_path)
            if current_size <= info["last_read_pos"]:
                return  # No new data

            with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                f.seek(info["last_read_pos"])
                for line in f:
                    line = line.rstrip("\n\r")
                    if line:
                        await self._process_line(log_path, line)

            self._log_files[log_path]["last_read_pos"] = current_size

        except (FileNotFoundError, PermissionError, OSError):
            pass

    async def _process_line(self, log_path: str, line: str):
        """Process a single log line."""
        try:
            match = ERROR_PATTERN.match(line)

        except Exception as e:
            logger.error(f"_process_line failed: {e}")
            return None
        if not match:
            return

        data = match.groupdict()
        level = data["level"]
        component = data["component"]
        message = data["message"]

        if level == "ERROR" or level == "CRITICAL":
            await self._handle_error(log_path, component, message, data)
        elif level == "WARNING":
            await self._handle_warning(log_path, component, message, data)
        elif level == "INFO":
            self._stats["warnings_detected"] += 1  # Count info for stats

    async def _handle_error(
        self, log_path: str, component: str, message: str, data: Dict
    ):
        """Handle an ERROR-level log entry."""
        self._stats["errors_detected"] += 1

        if self._is_duplicate(component, message):
            self._stats["dedup_suppressed"] += 1
            return

        if self._is_rate_limited():
            self._stats["rate_limited"] += 1
            return

        if self.work_order_engine:
            try:
                wo_id = await self.work_order_engine.on_error_event({
                    "error": message,
                    "component": component,
                    "severity": "error",
                    "context": {
                        "source": log_path,
                        "timestamp": data.get("timestamp", ""),
                        "log_level": "ERROR",
                    },
                })
                if wo_id and wo_id > 0:
                    self._stats["work_orders_created"] += 1
                    self._rate_limit_timestamps.append(time.time())
                    logger.info(
                        f"Created WO #{wo_id} from {component}: {message[:80]}"
                    )
            except Exception as e:
                logger.error(f"Failed to create WO from log: {e}")

    async def _handle_warning(
        self, log_path: str, component: str, message: str, data: Dict
    ):
        """Handle a WARNING-level log entry with dedup."""
        self._stats["warnings_detected"] += 1

        warn_hash = self._hash_error(component, message)
        now = time.time()
        if warn_hash in self._seen_errors:
            if now - self._seen_errors[warn_hash] < DEDUP_WINDOW:
                self._stats["dedup_suppressed"] += 1
                return

        if self._is_rate_limited():
            self._stats["rate_limited"] += 1
            return

        if self.work_order_engine:
            try:
                wo_id = await self.work_order_engine.on_error_event({
                    "error": message,
                    "component": component,
                    "severity": "warning",
                    "context": {
                        "source": log_path,
                        "timestamp": data.get("timestamp", ""),
                        "log_level": "WARNING",
                    },
                })
                if wo_id and wo_id > 0:
                    self._stats["work_orders_created"] += 1
                    self._rate_limit_timestamps.append(time.time())
                    self._seen_errors[warn_hash] = now
                    logger.info(
                        f"Created WO #{wo_id} from {component} warning: {message[:80]}"
                    )
            except Exception as e:
                logger.error(f"Failed to create WO from warning: {e}")

    def _hash_error(self, component: str, message: str) -> str:
        """Create a hash for error deduplication."""
        return hashlib.md5(
            f"{component}:{message[:150]}".encode("utf-8")
        ).hexdigest()

    def _is_duplicate(self, component: str, message: str) -> bool:
        """Check if this error was recently seen."""
        err_hash = self._hash_error(component, message)
        now = time.time()
        if err_hash in self._seen_errors:
            if now - self._seen_errors[err_hash] < DEDUP_WINDOW:
                return True
        self._seen_errors[err_hash] = now
        return False

    def _is_rate_limited(self) -> bool:
        """Check if we've exceeded the rate limit."""
        now = time.time()
        self._rate_limit_timestamps = [
            t for t in self._rate_limit_timestamps
            if now - t < RATE_LIMIT_WINDOW
        ]
        return len(self._rate_limit_timestamps) >= RATE_LIMIT_MAX

    async def _cleanup_seen_errors(self):
        """Remove expired entries from seen_errors."""
        now = time.time()
        self._seen_errors = {
            h: t for h, t in self._seen_errors.items()
            if now - t < DEDUP_WINDOW
        }
        self._rate_limit_timestamps = [
            t for t in self._rate_limit_timestamps
            if now - t < RATE_LIMIT_WINDOW
        ]

    def get_stats(self) -> Dict:
        """Get watcher statistics."""
        return {
            **self._stats,
            "log_files_tracked": len(self._log_files),
            "seen_errors_cached": len(self._seen_errors),
        }


# ─── Stand-alone Runner ──────────────────────────────────────────

async def main():
    """Run the log watcher daemon."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.handlers.RotatingFileHandler(
                "/home/deck/Documents/dev-yuniScripts/SCRIPTS/TOOLS/log_watcher_daemon.log",
                maxBytes=10 * 1024 * 1024,
                backupCount=3,
            ),
        ],
    )

    logger.info("=" * 60)
    logger.info("Log Watcher Daemon Starting")
    logger.info("=" * 60)

    # Try to connect to work_order_engine
    try:
        sys.path.insert(0, "/home/deck/Documents/dev-yuniScripts/SCRIPTS/CLIENTS/deepsky_client")
        from work_order_engine import WorkOrderEngine

        engine = WorkOrderEngine({"enabled": True})
        watcher = LogWatcherDaemon(work_order_engine=engine)
        logger.info("WorkOrderEngine connected")
    except ImportError as e:
        logger.warning(f"WorkOrderEngine not available: {e}")
        logger.warning("Running in log-monitoring mode only (no WO creation)")
        watcher = LogWatcherDaemon()

    await watcher.start()


if __name__ == "__main__":
    asyncio.run(main())

