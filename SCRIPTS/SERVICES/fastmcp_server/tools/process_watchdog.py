#!/usr/bin/env python3

# ── Dynamic Config Registration ──────────────────────────────
try:
    from dynamic_config_loader import register_configs
    register_configs("process_watchdog", [
        {"key": "check_interval_seconds", "type": "int", "default": 5,
          "description": "Process check interval",
          "valid_range": (1, 300),
          "category": "performance"},
        {"key": "max_process_age_seconds", "type": "int", "default": 3600,
          "description": "Max process age before force kill",
          "valid_range": (60, 86400),
          "category": "security"},
        {"key": "emergency_kill_enabled", "type": "bool", "default": True,
          "description": "Enable emergency process killing",
          "category": "security"},
        {"key": "logging_level", "type": "str", "default": 'INFO',
          "description": "Logging verbosity",
          "valid_options": ['DEBUG', 'INFO', 'WARNING', 'ERROR'],
          "category": "debug"},
    ])
except ImportError:
    pass
# ──────────────────────────────────────────────────────────────

"""
tools/process_watchdog.py — Background Process Watchdog Daemon

A background thread that periodically:
1. Scans for zombie/lingering processes
2. Logs warnings when problematic processes are found
3. Auto-cleans zombie processes (reaping via SIGCHLD to parents)
4. Maintains a process history database for trend analysis
5. Reports issues to the job manager's display engine

This module is designed to be imported and started by fastmcp_server_unified.py
during server initialization. It runs as a daemon thread with configurable
scan interval.

The watchdog is PURELY MONITORING by default — it only reports issues.
Auto-cleanup must be explicitly enabled in config.
"""

import os
import time
import json
import logging
import threading
from pathlib import Path
from typing import Optional, Dict, Any, List
from datetime import datetime

from ecosystem_os_abstraction import process_kill, process_info, Signals
logger = logging.getLogger("process_watchdog")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
# Known benign process names to exclude from watchdog warnings
# These are harmless processes that can safely be ignored.
# Format: list of substrings to match against process command lines.
KNOWN_BENIGN_EXCLUDES = [
    'zypak-sandbox',   # Brave/Chromium sandbox zombies (harmless)
    'zypak-helper',    # Zypak helper processes
    'chrome_crashpad', # Chrome crash handler
    'cat',             # cat commands (often harmless orphans)
]

SERVER_PID = os.getpid()
DEFAULT_SCAN_INTERVAL = 300  # seconds between scans
PROBLEMATIC_STATES = {'Z', 'T', 't', 'D'}
STUCK_MIN_RUNTIME = 60  # seconds before a process is considered "stuck"
MAX_ZOMBIE_WARNINGS = 3  # consecutive warnings before suggesting action


class ProcessWatchdog:
    """
    Background process monitor daemon.
    
    Runs in a separate daemon thread and periodically scans /proc for
    problematic processes. Logs warnings and optionally auto-cleans.
    """
    
    def __init__(self, scan_interval: int = DEFAULT_SCAN_INTERVAL,
                 auto_clean_zombies: bool = False,
                 auto_clean_orphans: bool = False,
                 auto_clean_threshold: int = 300):
        """
        Initialize the process watchdog.
        
        Args:
            scan_interval: Seconds between scans (default 60).
            auto_clean_zombies: If True, attempt auto-reap of zombies.
            auto_clean_orphans: If True, clean orphaned processes.
            auto_clean_threshold: Seconds of uptime before auto-clean considers a process.
        """
        self.scan_interval = scan_interval
        self.auto_clean_zombies = auto_clean_zombies
        self.auto_clean_orphans = auto_clean_orphans
        self.auto_clean_threshold = auto_clean_threshold
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._zombie_count_history: List[int] = []
        self._scan_count = 0
        
    def start(self):
        """Start the watchdog daemon thread."""
        if self._thread and self._thread.is_alive():
            logger.debug("Process watchdog already running")
            return
        
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._watchdog_loop,
            daemon=True,
            name="ProcessWatchdog"
        )
        self._thread.start()
        logger.info(f"Process watchdog started (interval={self.scan_interval}s, "
                    f"auto_clean={'zombies,orphans' if self.auto_clean_zombies and self.auto_clean_orphans
                              else 'zombies' if self.auto_clean_zombies
                              else 'orphans' if self.auto_clean_orphans
                              else 'off'})")
    
    def stop(self):
        """Stop the watchdog thread."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5.0)
            self._thread = None
        logger.info("Process watchdog stopped")
    
    def _watchdog_loop(self):
        """Main watchdog loop — runs in daemon thread."""
        while not self._stop_event.is_set():
            try:
                self._perform_scan()
            except Exception as e:
                logger.error(f"Process watchdog scan error: {e}")
            
            # Wait for next interval or stop signal
            self._stop_event.wait(self.scan_interval)
    
    def _perform_scan(self):
        """Perform a single scan of all processes."""
        self._scan_count += 1
        
        # Gather process data
        zombies = []
        orphans = []
        problematic = []
        server_children_stuck = []
        
        try:
            for entry in Path('/proc').iterdir():
                if not entry.name.isdigit():
                    continue
                
                pid = int(entry.name)
                if pid == SERVER_PID:
                    continue
                
                try:
                    # Read /proc/[pid]/stat
                    stat_path = entry / 'stat'
                    if not stat_path.is_file():
                        continue
                    
                    content = stat_path.read_text()
                    first_paren = content.index('(')
                    last_paren = content.rindex(')')
                    rest = content[last_paren + 2:].split()
                    
                    state_char = rest[0] if len(rest) > 0 else '?'
                    ppid = int(rest[1]) if len(rest) > 1 else 0
                    
                    # Get process name
                    cmdline_path = entry / 'cmdline'
                    cmdline = ''
                    if cmdline_path.is_file():
                        cmd_content = cmdline_path.read_text(errors='replace')
                        cmdline = ' '.join(a for a in cmd_content.split('\x00') if a)
                    
                    # Get uptime
                    clk_tck = 100
                    try:
                        clk_tck = os.sysconf(os.sysconf_names['SC_CLK_TCK'])
                    except Exception:
                        pass
                    
                    u_time = __import__('time').time()
                    uptime_content = __import__("pathlib").Path("/proc/uptime").read_text()
                    system_uptime = float(uptime_content.split()[0])
                    start_jiffies = int(rest[19]) if len(rest) > 19 else 0
                    proc_uptime = system_uptime - (start_jiffies / clk_tck)
                    
                    # Check if process is in known benign exclude list
                    if any(excl in cmdline for excl in KNOWN_BENIGN_EXCLUDES):
                        continue
                    
                    # Check state
                    if state_char == 'Z':
                        zombies.append({
                            'pid': pid, 'ppid': ppid, 'name': cmdline[:60],
                            'uptime': round(proc_uptime, 1)
                        })
                    
                    if state_char in PROBLEMATIC_STATES and proc_uptime > STUCK_MIN_RUNTIME:
                        problematic.append({
                            'pid': pid, 'ppid': ppid, 'state': state_char,
                            'name': cmdline[:60], 'uptime': round(proc_uptime, 1)
                        })
                    
                    # Check orphan (parent dead)
                    if ppid > 1 and state_char not in ('Z',):
                        try:
                            os.kill(ppid, 0)
                        except ProcessLookupError:
                            if proc_uptime > STUCK_MIN_RUNTIME:
                                orphans.append({
                                    'pid': pid, 'ppid': ppid, 'name': cmdline[:60],
                                    'uptime': round(proc_uptime, 1)
                                })
                    
                    # Check if server-child and stuck
                    if proc_uptime > 300 and state_char not in ('S', 'I'):
                        # Walk up parent chain
                        check_pid = ppid
                        visited = set()
                        while check_pid > 0 and check_pid not in visited:
                            visited.add(check_pid)
                            if check_pid == SERVER_PID:
                                server_children_stuck.append({
                                    'pid': pid, 'ppid': ppid, 'state': state_char,
                                    'name': cmdline[:60], 'uptime': round(proc_uptime, 1)
                                })
                                break
                            try:
                                gp_info = process_info(check_pid)
                                gp_first = gp_stat.index('(')
                                gp_last = gp_stat.rindex(')')
                                gp_rest = gp_stat[gp_last + 2:].split()
                                check_pid = int(gp_rest[1]) if len(gp_rest) > 1 else 0
                            except Exception:
                                break
                            
                except (ValueError, IndexError, PermissionError, 
                        ProcessLookupError, FileNotFoundError):
                    continue
        except PermissionError:
            logger.warning("Process watchdog: permission denied scanning /proc")
            return
        
        # Track zombie count history
        self._zombie_count_history.append(len(zombies))
        if len(self._zombie_count_history) > 10:
            self._zombie_count_history.pop(0)
        
        # -- Log findings --
        if zombies:
            logger.warning(
                f"Process watchdog: {len(zombies)} zombie process(es) detected "
                f"(scan #{self._scan_count})"
            )
            for z in zombies[:5]:  # Log first 5
                logger.debug(f"  Zombie: PID={z['pid']}, PPID={z['ppid']}, {z['name']}")
            if len(zombies) > 5:
                logger.debug(f"  ... and {len(zombies) - 5} more zombies")
            
            # Check if zombies are persistent
            if (len(self._zombie_count_history) >= MAX_ZOMBIE_WARNINGS and
                all(c > 0 for c in self._zombie_count_history[-MAX_ZOMBIE_WARNINGS:])):
                logger.warning(
                    f"Zombie processes persist after {MAX_ZOMBIE_WARNINGS} scans! "
                    f"Run process_cleanup(kill_zombies=True) or check parent processes."
                )
        
        if problematic:
            logger.warning(
                f"Process watchdog: {len(problematic)} problematic process(es) "
                f"(zombie/stopped/uninterruptible)"
            )
        
        if orphans:
            logger.info(
                f"Process watchdog: {len(orphans)} orphaned process(es) detected"
            )
        
        if server_children_stuck:
            logger.warning(
                f"Process watchdog: {len(server_children_stuck)} server-child process(es) "
                f"appear stuck or long-running"
            )
            for sc in server_children_stuck[:3]:
                logger.debug(f"  Stuck child: PID={sc['pid']}, {sc['name']}, "
                           f"uptime={sc['uptime']}s, state={sc['state']}")
        
        # -- Auto-cleanup (if enabled) --
        if self.auto_clean_zombies and zombies:
            self._auto_reap_zombies(zombies)
        
        if self.auto_clean_orphans and orphans:
            self._auto_clean_orphans(orphans)
    
    def _auto_reap_zombies(self, zombies: List[dict]):
        """Attempt to reap zombie processes by notifying parents."""
        reaped = 0
        for zombie in zombies:
            try:
                pid = zombie['pid']
                ppid = zombie['ppid']
                
                # Send SIGCHLD to parent to encourage reaping
                if ppid > 1:
                    try:
                        os.kill(ppid, signal.SIGCHLD)
                        reaped += 1
                    except (PermissionError, ProcessLookupError):
                        pass
                
                # If parent is dead or our SIGCHLD didn't work,
                # check if parent is a server child and terminate it
                if ppid > 1:
                    try:
                        os.kill(ppid, 0)  # Check if parent exists
                    except ProcessLookupError:
                        # Parent gone — init will reap
                        pass
            except Exception:
                continue
        
        if reaped > 0:
            logger.info(f"Process watchdog: sent SIGCHLD to {reaped} parent(s) to reap zombies")
    
    def _auto_clean_orphans(self, orphans: List[dict]):
        """Auto-clean orphaned processes that are stuck."""
        import signal as _signal
        cleaned = 0
        for orphan in orphans:
            if orphan['uptime'] > self.auto_clean_threshold:
                try:
                    os.kill(orphan['pid'], _signal.SIGTERM)
                    cleaned += 1
                except (PermissionError, ProcessLookupError):
                    continue
        
        if cleaned > 0:
            logger.info(f"Process watchdog: auto-cleaned {cleaned} orphaned process(es)")
    
    def get_status(self) -> Dict[str, Any]:
        """Get watchdog status report."""
        return {
            'running': self._thread is not None and self._thread.is_alive(),
            'scan_interval': self.scan_interval,
            'scans_performed': self._scan_count,
            'auto_clean_zombies': self.auto_clean_zombies,
            'auto_clean_orphans': self.auto_clean_orphans,
            'zombie_history': self._zombie_count_history[-5:] if self._zombie_count_history else [],
            'consecutive_zombie_warnings': min(len(self._zombie_count_history), MAX_ZOMBIE_WARNINGS)
                if all(c > 0 for c in self._zombie_count_history[-MAX_ZOMBIE_WARNINGS:])
                else 0,
        }


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------
_watchdog_instance: Optional[ProcessWatchdog] = None


def get_watchdog() -> ProcessWatchdog:
    """Get the global process watchdog instance."""
    global _watchdog_instance
    if _watchdog_instance is None:
        _watchdog_instance = ProcessWatchdog()
    return _watchdog_instance


def start_watchdog(config: Optional[Dict[str, Any]] = None):
    """
    Start the process watchdog with optional configuration.
    
    Args:
        config: Configuration dict with keys:
            - process_watchdog_interval (int): Scan interval in seconds
            - process_watchdog_auto_clean_zombies (bool): Auto-reap zombies
            - process_watchdog_auto_clean_orphans (bool): Auto-clean orphans
    
    Returns:
        The watchdog instance.
    """
    watchdog = get_watchdog()
    
    if config:
        watchdog.scan_interval = config.get('process_watchdog_interval', DEFAULT_SCAN_INTERVAL)
        watchdog.auto_clean_zombies = config.get('process_watchdog_auto_clean_zombies', False)
        watchdog.auto_clean_orphans = config.get('process_watchdog_auto_clean_orphans', False)
        watchdog.auto_clean_threshold = config.get('process_watchdog_auto_clean_threshold', 300)
    
    watchdog.start()
    return watchdog


def stop_watchdog():
    """Stop the global process watchdog."""
    global _watchdog_instance
    if _watchdog_instance:
        _watchdog_instance.stop()
        _watchdog_instance = None
