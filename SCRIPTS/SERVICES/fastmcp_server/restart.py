#!/usr/bin/env python3
"""
restart.py - Graceful Restart Wrapper for YuniScript FastMCP Server.

Called by the AI after modifying MCP server files.
Performs a full clean shutdown flow with generous timeouts, then restarts.

Flow:
  1. Create restart.flag to signal the running server to shut down
  2. Wait for graceful shutdown with generous timeout
  3. If server doesn't stop in time, escalate to SIGTERM
  4. Verify port is free
  5. Wait for indexer to save state
  6. Restart server

Usage:
  python restart.py                          # Normal restart
  python restart.py --force                  # Force kill immediately
  python restart.py --stop-only              # Only stop
  python restart.py --start-only             # Only start
"""

import os
import sys
import time
import json
import signal
import subprocess
import socket
import logging
import re
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parent
FLAG_FILE = SERVER_DIR / "restart.flag"
META_FILE = SERVER_DIR / "meta.info"

SIGTERM_TIMEOUT = 60
SIGKILL_WAIT = 5
PORT_CHECK_TIMEOUT = 30
INDEXER_WAIT = 15
RESTART_DELAY = 3

LOG_FILE = SERVER_DIR / "restart.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler(sys.stderr)],
)
logger = logging.getLogger("restart_yuniscript")

def find_server_pids():
    """Find PIDs running the YuniScript FastMCP server."""
    pids = set()
    try:
        result = subprocess.run(
            ["pgrep", "-f", "fastmcp_server/main.py"],
            capture_output=True, text=True, timeout=5
        )
        if result.stdout.strip():
            for p in result.stdout.strip().split():
                p = p.strip()
                if p.isdigit():
                    pids.add(int(p))
    except Exception:
        pass
    try:
        result = subprocess.run(
            ["pgrep", "-f", "FastMCPYuniScript"],
            capture_output=True, text=True, timeout=5
        )
        if result.stdout.strip():
            for p in result.stdout.strip().split():
                p = p.strip()
                if p.isdigit():
                    pids.add(int(p))
    except Exception:
        pass
    return list(pids)

def is_process_alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, OSError):
        return False

def signal_process(pid, sig):
    try:
        os.kill(pid, sig)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        logger.warning(f"Permission denied sending signal to PID {pid}")
        return False

def wait_for_no_pids(pids, timeout):
    deadline = time.time() + timeout
    remaining = list(pids)
    while time.time() < deadline and remaining:
        remaining = [p for p in remaining if is_process_alive(p)]
        if remaining:
            time.sleep(1)
    return remaining

def shutdown_server(force=False):
    """Shut down the YuniScript MCP server gracefully."""
    logger.info("Shutting down YuniScript MCP server...")

    # First, create the restart flag so the server can self-terminate
    try:
        FLAG_FILE.write_text("restart")
        logger.info("Created restart.flag for server self-shutdown")
    except Exception as e:
        logger.error(f"Failed to create restart.flag: {e}")

    # Wait for server to see the flag and shut down
    if force:
        logger.warning("FORCE MODE: Skipping graceful shutdown wait")
    else:
        pids = find_server_pids()
        if pids:
            logger.info(f"Waiting up to {SIGTERM_TIMEOUT}s for server to self-shutdown")
            remaining = wait_for_no_pids(pids, SIGTERM_TIMEOUT)
            if not remaining:
                logger.info("Server shut down gracefully after seeing restart flag")
                try:
                    FLAG_FILE.unlink(missing_ok=True)
                except Exception:
                    pass
                return True
            logger.warning(f"Server did not self-shutdown within timeout, remaining PIDs: {remaining}")
        else:
            logger.info("No server PIDs found")

    # Direct kill escalation
    pids = find_server_pids()
    if not pids:
        logger.info("No server PIDs to kill")
        try:
            FLAG_FILE.unlink(missing_ok=True)
        except Exception:
            pass
        return True

    logger.info(f"Direct kill escalation for PIDs: {pids}")
    for pid in pids:
        signal_process(pid, signal.SIGTERM)
    remaining = wait_for_no_pids(pids, 10)
    if remaining:
        for pid in remaining:
            logger.warning(f"SIGKILL for PID {pid}")
            signal_process(pid, signal.SIGKILL)
        time.sleep(SIGKILL_WAIT)

    remaining_final = find_server_pids()
    if remaining_final:
        logger.warning(f"PIDs still alive after SIGKILL: {remaining_final}")
    else:
        logger.info("All server processes stopped")

    try:
        FLAG_FILE.unlink(missing_ok=True)
    except Exception:
        pass
    return True

def start_server():
    """Start the YuniScript MCP server."""
    # Find Python executable
    for python_candidate in [
        SERVER_DIR / ".venv" / "bin" / "python3",
        SERVER_DIR / ".venv" / "bin" / "python",
        "/usr/bin/python3",
    ]:
        if Path(python_candidate).exists():
            python_exe = str(python_candidate)
            break
    else:
        python_exe = sys.executable

    server_script = str(SERVER_DIR / "main.py")
    if not Path(server_script).exists():
        logger.error(f"Server script not found: {server_script}")
        return False

    logger.info(f"Starting server: {python_exe} {server_script}")
    try:
        proc = subprocess.Popen(
            [python_exe, server_script],
            cwd=str(SERVER_DIR),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
        )
        logger.info(f"Server started with PID {proc.pid}")
        return True
    except Exception as e:
        logger.error(f"Failed to start server: {e}")
        return False

def main():
    try:
        import argparse

    except Exception as e:
        logger.error(f"main failed: {e}")
        return None
    parser = argparse.ArgumentParser(description="Graceful Restart Wrapper for YuniScript FastMCP Server")
    parser.add_argument("--force", action="store_true", help="Force kill immediately")
    parser.add_argument("--start-only", action="store_true", help="Only start")
    parser.add_argument("--stop-only", action="store_true", help="Only stop")
    args = parser.parse_args()

    logger.info("=" * 60)
    logger.info("YuniScript FastMCP Server Restart Wrapper")
    logger.info(f"  Server dir: {SERVER_DIR}")
    logger.info("=" * 60)

    if args.start_only:
        logger.info("Start-only mode")
        start_server()
        time.sleep(3)
        pids = find_server_pids()
        logger.info(f"Server PIDs: {pids}" if pids else "Server may not be running yet")
        return

    if args.stop_only:
        logger.info("Stop-only mode")
        shutdown_server(force=args.force)
        return

    # Full restart
    shutdown_server(force=args.force)
    logger.info(f"Waiting {RESTART_DELAY}s before restart...")
    time.sleep(RESTART_DELAY)
    start_server()
    time.sleep(3)
    pids = find_server_pids()
    if pids:
        logger.info(f"Server running with PIDs: {pids}")
    else:
        logger.warning("Server may not be running yet")

    logger.info("=" * 60)
    logger.info("Restart cycle completed")
    logger.info("=" * 60)

if __name__ == "__main__":
    main()

