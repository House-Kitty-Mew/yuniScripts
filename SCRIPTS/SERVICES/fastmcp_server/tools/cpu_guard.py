
# ── Dynamic Config Registration ──────────────────────────────
try:
    from dynamic_config_loader import register_configs
    register_configs("cpu_guard", [
        {"key": "cpu_threshold_pct", "type": "int", "default": 80,
         "description": "CPU usage threshold % before throttling",
         "valid_range": (10, 100), "category": "performance"},
        {"key": "memory_threshold_mb", "type": "int", "default": 256,
         "description": "Memory threshold MB before action",
         "valid_range": (64, 4096), "category": "performance"},
        {"key": "check_interval_seconds", "type": "int", "default": 5,
         "description": "CPU check interval", "valid_range": (1, 60),
         "category": "performance"},
        {"key": "max_concurrent_jobs", "type": "int", "default": 4,
         "description": "Max concurrent processing jobs",
         "valid_range": (1, 32), "category": "performance"},
        {"key": "high_cpu_signal_threshold", "type": "float", "default": 0.8,
         "description": "Signal threshold for high CPU",
         "valid_range": (0.5, 1.0), "category": "monitoring"},
        {"key": "critical_cpu_threshold", "type": "float", "default": 0.95,
         "description": "Critical CPU threshold for emergency action",
         "valid_range": (0.8, 1.0), "category": "security"},
        {"key": "debug_enabled", "type": "bool", "default": False,
         "description": "Enable CPU guard debug logging",
         "category": "debug"},
    ])
except ImportError:
    pass
# ──────────────────────────────────────────────────────────────

"""
tools/cpu_guard.py — CPU Guard + Zombie Thread Detection + Load-Based Throttling

Provides runtime CPU protection for the FastMCP server:
  1. Per-job CPU usage monitoring (reads /proc/self/stat per thread)
  2. Automatic throttling when a job exceeds 10% of a physical core for >30s
  3. Zombie thread detection (threads alive but not making progress for >120s)
  4. System load-based job throttling (checks /proc/loadavg)

Usage:
    from cpu_guard import CPUGuard, CPUGuardContext
    
    guard = CPUGuard()
    
    # Use as context manager around a job
    with guard.watch(job_id="abc123", tool_name="my_tool") as ctx:
        do_work()
        ctx.report_progress(50)  # Reports activity (anti-zombie)
"""

import os
import time
import json
import threading
import logging
from typing import Optional, Dict, Any, List
from pathlib import Path

from ecosystem_os_abstraction import system_loadavg, process_info, process_kill, system_memory
logger = logging.getLogger("cpu_guard")

# ── Configuration defaults ───────────────────────────────────────
# All configurable via config table keys cpu_guard.*

CPU_GUARD_ENABLED = True
"""Master switch for all CPU guard features."""

MAX_CPU_PERCENT_PER_JOB = 30.0
"""Max % of a single physical core per job before throttling."""

CPU_THROTTLE_WINDOW = 30.0
"""Seconds of sustained CPU overuse before throttling kicks in."""

CPU_KILL_WINDOW = 60.0
"""Seconds of sustained CPU overuse before job is killed."""

CPU_KILL_THRESHOLD = 80.0
"""CPU % threshold that triggers kill (higher than throttle threshold)."""

ZOMBIE_STALE_SECONDS = 120.0
"""Seconds without progress update before a thread is flagged as zombie."""

LOAD_THROTTLE_RATIO = 0.95
"""Fraction of CPU count for /proc/loadavg threshold. 0.8 = 80%."""

LOAD_CHECK_INTERVAL = 5.0
"""Seconds between /proc/loadavg checks."""

# ═══════════════════════════════════════════════════════════════════
# CPU time reading from /proc/self/stat
# ═══════════════════════════════════════════════════════════════════

_CLK_TCK = os.sysconf(os.sysconf_names['SC_CLK_TCK']) if hasattr(os, 'sysconf') else 100
_NUM_CORES = os.cpu_count() or 1
_MAX_CORE_TICKS_PER_SEC = _CLK_TCK  # 1 core = CLK_TCK ticks/sec


def _read_process_cpu_times() -> tuple:
    """
    Read CPU time from /proc/self/stat.
    Returns (utime_ticks, stime_ticks, cutime_ticks, cstime_ticks).
    All values in clock ticks (CLK_TCK per second).
    """
    try:
        pinfo = process_info(os.getpid())
        # Use /proc/self/stat via process_info as fallback
        utime = 0; stime = 0; cutime = 0; cstime = 0
        if pinfo.get('exists') and pinfo.get('cpu_time'):
            total_cpu = pinfo['cpu_time']
            utime = int(total_cpu * _CLK_TCK / 2) if total_cpu > 0 else 0
            stime = utime
        return (utime, stime, cutime, cstime)
    except Exception as e:
        logger.debug(f"cpu_guard: Cannot read CPU times: {e}")
        return (0, 0, 0, 0)


def _get_system_load() -> float:
    """Get the 1-minute load average from /proc/loadavg. Returns 0.0 on failure."""
    try:
        loads = system_loadavg()
        return loads[0] if loads and len(loads) >= 1 else 0.0
    except Exception:
        return 0.0


def _is_overloaded(load: float = None) -> bool:
    """Check if system load exceeds LOAD_THROTTLE_RATIO of CPU count."""
    if load is None:
        load = _get_system_load()
    threshold = _NUM_CORES * LOAD_THROTTLE_RATIO
    return load > threshold


# ═══════════════════════════════════════════════════════════════════
# Per-job CPU monitoring context
# ═══════════════════════════════════════════════════════════════════

class CPUGuardContext:
    """Context manager for monitoring a single job's CPU usage."""

    def __init__(self, job_id: str, tool_name: str, guard: 'CPUGuard'):
        self.job_id = job_id
        self.tool_name = tool_name
        self.guard = guard
        self._start_cpu = _read_process_cpu_times()
        self._start_time = time.time()
        self._last_activity_time = time.time()
        self._last_progress = 0.0
        self._progress_stale_count = 0
        self._throttle_triggered = False
        self._kill_triggered = False
        self._warnings: List[str] = []
        self._active = True

    def report_progress(self, progress: float):
        """Report job progress to prevent zombie detection."""
        self._last_activity_time = time.time()
        if progress > self._last_progress:
            self._last_progress = progress
            self._progress_stale_count = 0
        else:
            self._progress_stale_count += 1

    def _check_cpu(self) -> float:
        """Calculate CPU usage % of a single core since start."""
        current_cpu = _read_process_cpu_times()
        elapsed = time.time() - self._start_time
        if elapsed <= 0:
            return 0.0
        delta_ticks = sum(current_cpu) - sum(self._start_cpu)
        # CPU % of one core = (delta_ticks / CLK_TCK) / elapsed * 100
        cpu_pct = (delta_ticks / _CLK_TCK) / elapsed * 100.0
        return min(cpu_pct, 100.0 * _NUM_CORES)  # Cap at total system CPU

    def _check_zombie(self) -> bool:
        """Check if this thread has become a zombie (stale for too long)."""
        if not self._active:
            return False
        stale = time.time() - self._last_activity_time
        return stale > ZOMBIE_STALE_SECONDS

    def _check_health(self) -> Dict[str, Any]:
        """
        Check job health using SUSPICION SCORE system.
        
        Returns dict with:
          - healthy: bool (True if OK, False if needs action)
          - cpu_pct: current CPU usage %
          - is_zombie: True if thread is zombie
          - is_overloaded: True if system overloaded
          - action: 'ok' | 'watch' | 'throttle' | 'warn' | 'kill' | 'zombie'
          - warnings: list of warning strings
          - suspicion_score: 0-100 computed suspicion score
          - suspicion_signals: dict of triggered signals
        """
        result = {
            'healthy': True,
            'cpu_pct': 0.0,
            'is_zombie': False,
            'is_overloaded': False,
            'action': 'ok',
            'warnings': [],
            'suspicion_score': 0.0,
            'suspicion_signals': {},
        }

        # ── Collect signals ──
        cpu_pct = self._check_cpu()
        result['cpu_pct'] = round(cpu_pct, 1)
        elapsed = time.time() - self._start_time
        
        suspicion = 0.0
        signals = {}
        
        # Read memory usage from /proc/self/status VmRSS
        try:
            mem = system_memory()
            self._memory_mb = mem.get('used_kb', 0) / 1024.0
        except Exception:
            self._memory_mb = 0.0
        
        # Count child processes (pids in /proc matching our tgid)
        try:
            our_pid = os.getpid()
            child_count = 0
            for proc in os.listdir('/proc'):
                if proc.isdigit() and proc != str(our_pid):
                    try:
                        proc_info = process_info(int(proc))
                        if proc_info.get('exists') and proc_info.get('ppid') == our_pid:
                            child_count += 1
                    except Exception:
                        pass
            self._child_processes = child_count
        except Exception:
            self._child_processes = 0
        
        # ═══ Signal 1: Sustained high CPU ═══
        if cpu_pct > 70.0 and elapsed > 30:
            signals['HIGH_CPU'] = {'score': 15, 'detail': f'CPU {cpu_pct:.1f}% >70% for {elapsed:.0f}s'}
            suspicion += 15
        
        # ═══ Signal 2: Critical CPU (very sus) ═══
        if cpu_pct > 90.0 and elapsed > 60:
            signals['CRITICAL_CPU'] = {'score': 30, 'detail': f'CPU {cpu_pct:.1f}% >90% for {elapsed:.0f}s'}
            suspicion += 30
        
        # ═══ Signal 3: High memory usage ═══
        if self._memory_mb > 500:
            signals['HIGH_MEMORY'] = {'score': 15, 'detail': f'Memory {self._memory_mb:.0f}MB >500MB'}
            suspicion += 15
        
        # ═══ Signal 4: Excessive child processes (fork bomb detection) ═══
        if self._child_processes > 10:
            signals['HIGH_PROCESS'] = {'score': 25, 'detail': f'{self._child_processes} child processes'}
            suspicion += 25
        
        # ═══ Signal 5: Combined CPU + memory (bonus for intersection) ═══
        if (signals.get('HIGH_CPU') or signals.get('CRITICAL_CPU')) and signals.get('HIGH_MEMORY'):
            signals['COMBINED_HIGH'] = {'score': 20, 'detail': 'High CPU + High Memory combined'}
            suspicion += 20
        
        # ═══ Signal 6: CPU + process spawn (fork bomb) ═══
        if (signals.get('HIGH_CPU') or signals.get('CRITICAL_CPU')) and signals.get('HIGH_PROCESS'):
            signals['FORK_BOMB'] = {'score': 50, 'detail': f'High CPU + {self._child_processes} child processes'}
            suspicion += 50
        
        # ═══ Signal 7: Sustained any stress >120s ──
        if cpu_pct > 50.0 and elapsed > 120:
            signals['SUSTAINED'] = {'score': 10, 'detail': f'Sustained >50% CPU for {elapsed:.0f}s'}
            suspicion += 10
        
        # Cap suspicion at 100
        suspicion = min(suspicion, 100.0)
        self._suspicion_score = suspicion
        
        result['suspicion_score'] = round(suspicion, 1)
        result['suspicion_signals'] = signals
        
        # ═══ Action decision based on suspicion score ═══
        #   0-20: OK        — no action (normal usage)
        #  20-40: WATCH     — log warning, increase monitoring
        #  40-60: THROTTLE  — reduce workers
        #  60-80: WARN      — log admin alert
        #  80+:   KILL      — terminate job (definitely sus)
        
        if suspicion >= 80:
            result['healthy'] = False
            result['action'] = 'kill'
            signal_detail = '; '.join(f"{k}({v['score']})" for k, v in signals.items())
            result['warnings'].append(
                f"SUSPICION SCORE {suspicion:.0f}/100: KILL — {signal_detail}"
            )
        elif suspicion >= 60:
            result['healthy'] = True
            result['action'] = 'warn'
            signal_detail = '; '.join(f"{k}({v['score']})" for k, v in signals.items())
            result['warnings'].append(
                f"SUSPICION SCORE {suspicion:.0f}/100: WARN — {signal_detail}"
            )
        elif suspicion >= 40:
            result['healthy'] = True
            result['action'] = 'throttle'
            result['warnings'].append(
                f"SUSPICION SCORE {suspicion:.0f}/100: THROTTLE — reducing workers"
            )
        elif suspicion >= 20:
            result['healthy'] = True
            result['action'] = 'watch'
            if signals:
                signal_detail = '; '.join(f"{k}({v['score']})" for k, v in signals.items())
                result['warnings'].append(
                    f"SUSPICION SCORE {suspicion:.0f}/100: WATCH — {signal_detail}"
                )
        
        # Zombie check (independent of suspicion)
        if self._check_zombie():
            result['healthy'] = False
            result['action'] = 'zombie'
            result['is_zombie'] = True
            result['warnings'].append(
                f"Zombie: no progress for {ZOMBIE_STALE_SECONDS:.0f}s"
            )
        
        # Load check (independent)
        load = _get_system_load()
        if _is_overloaded(load):
            result['is_overloaded'] = True
            result['warnings'].append(
                f"System overloaded: load={load:.2f} / cores={_NUM_CORES} * ratio={LOAD_THROTTLE_RATIO:.1f}"
            )
        
        # Log if suspicion > 0
        if suspicion >= 20:
            logger.info(
                f"CPU Guard suspicion: job={self.job_id} tool={self.tool_name} "
                f"suspicion={suspicion:.0f}/100 action={result['action']} "
                f"cpu={cpu_pct:.1f}% mem={self._memory_mb:.0f}MB children={self._child_processes}"
            )

        return result

    def close(self):
        """Mark job as ended. Stops health checks."""
        self._active = False


class CPUGuard:
    """
    CPU Guard system for monitoring and throttling jobs.
    
    Singleton pattern — use get_cpu_guard().
    """

    def __init__(self):
        self._jobs: Dict[str, CPUGuardContext] = {}
        self._lock = threading.RLock()
        self._monitor_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._monitor_interval = 5.0  # seconds between health checks
        self._load_window: List[float] = []  # rolling load history
        self._overloaded = False
        self._current_max_workers = 20  # tracks current throttled worker count
        self._normal_max_workers = 20

        # Stats
        self._total_jobs = 0
        self._total_throttles = 0
        self._total_zombies_detected = 0
        self._total_kills = 0

        logger.info(
            f"CPU Guard initialized: "
            f"max_cpu={MAX_CPU_PERCENT_PER_JOB}%, "
            f"cores={_NUM_CORES}, "
            f"clk_tck={_CLK_TCK}, "
            f"zombie_stale={ZOMBIE_STALE_SECONDS}s, "
            f"load_ratio={LOAD_THROTTLE_RATIO}"
        )

    def start_monitoring(self, max_workers: int = 10):
        """Start the background health monitor thread."""
        if self._monitor_thread and self._monitor_thread.is_alive():
            return
        self._normal_max_workers = max_workers
        self._current_max_workers = max_workers
        self._stop_event.clear()
        self._monitor_thread = threading.Thread(
            target=self._monitor_loop,
            daemon=True,
            name="CPUGuardMonitor"
        )
        self._monitor_thread.start()
        logger.info("CPU Guard monitoring started (interval=%ss)", self._monitor_interval)

    def stop_monitoring(self):
        """Stop the background health monitor."""
        self._stop_event.set()
        if self._monitor_thread:
            self._monitor_thread.join(timeout=3.0)
            self._monitor_thread = None
        logger.info("CPU Guard monitoring stopped")

    def watch(self, job_id: str, tool_name: str) -> CPUGuardContext:
        """Create and register a CPU guard context for a job."""
        ctx = CPUGuardContext(job_id, tool_name, self)
        with self._lock:
            self._jobs[job_id] = ctx
            self._total_jobs += 1
        return ctx

    def unwatch(self, job_id: str):
        """Remove a job from CPU monitoring."""
        with self._lock:
            if job_id in self._jobs:
                self._jobs[job_id].close()
                del self._jobs[job_id]

    def get_recommended_max_workers(self) -> int:
        """
        Get the recommended max worker count based on current load.
        Returns the current throttled value.
        """
        return self._current_max_workers

    def get_stats(self) -> Dict[str, Any]:
        """Get CPU Guard statistics."""
        load = _get_system_load()
        with self._lock:
            active_jobs = sum(1 for j in self._jobs.values() if j._active)
            return {
                'enabled': CPU_GUARD_ENABLED,
                'max_cpu_percent_per_job': MAX_CPU_PERCENT_PER_JOB,
                'cpu_throttle_window_s': CPU_THROTTLE_WINDOW,
                'cpu_kill_window_s': CPU_KILL_WINDOW,
                'zombie_stale_seconds': ZOMBIE_STALE_SECONDS,
                'load_throttle_ratio': LOAD_THROTTLE_RATIO,
                'num_cores': _NUM_CORES,
                'current_load_1min': round(load, 2),
                'overloaded': _is_overloaded(load),
                'normal_max_workers': self._normal_max_workers,
                'current_max_workers': self._current_max_workers,
                'active_jobs': active_jobs,
                'total_jobs': self._total_jobs,
                'total_throttles': self._total_throttles,
                'total_zombies_detected': self._total_zombies_detected,
                'total_kills': self._total_kills,
            }

    def _monitor_loop(self):
        """Background loop that checks health of all active jobs."""
        while not self._stop_event.is_set():
            try:
                self._perform_health_check()
            except Exception as e:
                logger.error(f"CPU Guard health check error: {e}")
            self._stop_event.wait(self._monitor_interval)

    def _perform_health_check(self):
        """Check health of all active jobs and apply throttling."""
        with self._lock:
            if not CPU_GUARD_ENABLED:
                return

            kill_list = []
            throttle_list = []
            zombie_list = []

            for job_id, ctx in list(self._jobs.items()):
                if not ctx._active:
                    continue
                health = ctx._check_health()

                if health['action'] == 'kill':
                    kill_list.append((job_id, health))
                elif health['action'] == 'throttle':
                    throttle_list.append((job_id, health))
                elif health['action'] == 'zombie':
                    zombie_list.append((job_id, health))

            # Handle zombies
            for job_id, health in zombie_list:
                self._total_zombies_detected += 1
                logger.warning(
                    f"CPU Guard ZOMBIE: job={job_id}, "
                    f"warnings={health['warnings']}"
                )

            # Handle kills
            for job_id, health in kill_list:
                self._total_kills += 1
                logger.warning(
                    f"CPU Guard KILL: job={job_id}, "
                    f"cpu={health['cpu_pct']}%, "
                    f"warnings={health['warnings']}"
                )
                # Mark for cancellation (caller should cancel the Future)
                ctx = self._jobs.get(job_id)
                if ctx:
                    ctx._kill_triggered = True

            # Handle throttles
            for job_id, health in throttle_list:
                self._total_throttles += 1
                ctx = self._jobs.get(job_id)
                if ctx and not ctx._throttle_triggered:
                    ctx._throttle_triggered = True
                    logger.warning(
                        f"CPU Guard THROTTLE: job={job_id}, "
                        f"cpu={health['cpu_pct']}%, "
                        f"tool={ctx.tool_name}"
                    )

            # System load-based throttling
            load = _get_system_load()
            self._load_window.append((time.time(), load))
            # Keep only last 30s of load data
            cutoff = time.time() - 30.0
            self._load_window = [(t, l) for t, l in self._load_window if t > cutoff]

            if _is_overloaded(load):
                if not self._overloaded:
                    self._overloaded = True
                    # Reduce workers when overloaded
                    reduced = max(1, self._normal_max_workers // 4)
                    self._current_max_workers = reduced
                    logger.warning(
                        f"CPU Guard: System overloaded (load={load:.2f}), "
                        f"reducing workers from {self._normal_max_workers} to {reduced}"
                    )
            else:
                if self._overloaded:
                    # Check if load has been normal for 30s
                    recent_loads = [l for _, l in self._load_window]
                    if all(not _is_overloaded(l) for l in recent_loads):
                        self._overloaded = False
                        self._current_max_workers = self._normal_max_workers
                        logger.info(
                            f"CPU Guard: Load normal (load={load:.2f}), "
                            f"restoring workers to {self._normal_max_workers}"
                        )

    def is_job_killed(self, job_id: str) -> bool:
        """Check if a job has been flagged for kill by the CPU guard."""
        with self._lock:
            ctx = self._jobs.get(job_id)
            return ctx is not None and ctx._kill_triggered


# ═══════════════════════════════════════════════════════════════════
# Singleton
# ═══════════════════════════════════════════════════════════════════

_global_cpu_guard = None
_guard_lock = threading.Lock()


def get_cpu_guard() -> CPUGuard:
    """Get the global CPU Guard singleton."""
    global _global_cpu_guard
    if _global_cpu_guard is None:
        with _guard_lock:
            if _global_cpu_guard is None:
                _global_cpu_guard = CPUGuard()
    return _global_cpu_guard


# ═══════════════════════════════════════════════════════════════════
# MCP Tool wrapper: reports CPU guard status
# ═══════════════════════════════════════════════════════════════════

def cpu_guard_status() -> str:
    """Get the current CPU Guard status and statistics.

    Returns:
        JSON string with CPU Guard stats.
    """
    guard = get_cpu_guard()
    return json.dumps(guard.get_stats(), indent=2)


# ═══════════════════════════════════════════════════════════════════
# Self-test
# ═══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys
    guard = get_cpu_guard()
    if '--stats' in sys.argv:
        print(json.dumps(guard.get_stats(), indent=2))
    elif '--load' in sys.argv:
        load = _get_system_load()
        print(f"Load: {load:.2f} / {_NUM_CORES} cores")
        print(f"Overloaded: {_is_overloaded(load)}")
    elif '--cpu' in sys.argv:
        before = _read_process_cpu_times()
        time.sleep(1)
        after = _read_process_cpu_times()
        print(f"Before: {before}")
        print(f"After: {after}")
        delta_ticks = sum(after) - sum(before)
        print(f"Delta ticks: {delta_ticks}")
        print(f"CLK_TCK: {_CLK_TCK}")
        cpu_pct = (delta_ticks / _CLK_TCK) / 1.0 * 100.0
        print(f"CPU %: {cpu_pct:.1f}%")
    else:
        print(f"CPU Guard v1.0 — {_NUM_CORES} cores, CLK_TCK={_CLK_TCK}")
        print(json.dumps(guard.get_stats(), indent=2))
