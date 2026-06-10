#!/usr/bin/env python3

# ── Dynamic Config Registration ──────────────────────────────
try:
    from dynamic_config_loader import register_configs
    register_configs("process_monitor", [
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
tools/process_monitor.py — Advanced OS Process Monitoring Tool

Provides comprehensive visibility into running OS processes with:
- Full process listing with PID, PPID, user, state, CPU, memory, runtime
- Zombie process detection
- Stuck/lingering process detection (processes that should have ended)
- Process hierarchy/tree view
- Server-child process identification
- Resource usage analysis (CPU hogs, memory hogs)
- Filtering by user, state, name pattern, age

Integrates with the job_manager to correlate running OS processes with
tracked job sessions. Flags orphaned/stuck processes for cleanup.

**Backup:** This is a READ-ONLY tool. No backup snapshots are created.
"""

import os
import time
import pwd
import json
import logging
from pathlib import Path
from typing import Optional, Any, Dict, List, Tuple

from ecosystem_os_abstraction import process_info, process_list, system_memory, system_loadavg
logger = logging.getLogger("process_monitor")


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
PROC_PATH = Path("/proc")
SERVER_PID = os.getpid()

# Process states (from /proc/[pid]/status)
PROCESS_STATES = {
    'R': 'RUNNING',
    'S': 'SLEEPING',
    'D': 'UNINTERRUPTIBLE',
    'Z': 'ZOMBIE',
    'T': 'STOPPED',
    't': 'TRACING_STOP',
    'X': 'DEAD',
    'x': 'DEAD',
    'K': 'WAKEKILL',
    'W': 'WAKING',
    'P': 'PARKED',
    'I': 'IDLE',
}

# Thresholds for 'stuck/lingering' detection
# A process is considered "stuck" if it's been running longer than this
# AND is in a problematic state (ZOMBIE, STOPPED, UNINTERRUPTIBLE)
STUCK_STATES = {'Z', 'T', 't', 'D'}
STUCK_MIN_RUNTIME = 30  # seconds
# A process is "lingering" if it's been alive longer than expected for its type
LINGERING_SHELL_MIN = 300  # 5 minutes for shell processes
LINGERING_PYTHON_MIN = 600  # 10 minutes for Python scripts
LINGERING_DEFAULT_MIN = 3600  # 1 hour for anything else

# Server-adjacent process name patterns
SERVER_CHILD_PATTERNS = [
    'python', 'python3', 'bash', 'sh', 'zsh',
    'sleep', 'ping', 'nc', 'ncat', 'socat',
    'node', 'npm', 'npx',
    'curl', 'wget',
    'git',
    'pip', 'pip3',
]


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------
def _read_proc_file(pid: int, filename: str) -> Optional[str]:
    """Read a file from /proc/[pid]/filename. Returns None on failure."""
    path = PROC_PATH / str(pid) / filename
    try:
        if path.is_file():
            return path.read_text(errors='replace')
    except (PermissionError, FileNotFoundError, ProcessLookupError, OSError):
        pass
    return None


def _parse_status(pid: int) -> Dict[str, str]:
    """Parse /proc/[pid]/status into a dict of key:value pairs."""
    content = _read_proc_file(pid, 'status')
    if not content:
        return {}
    
    result = {}
    for line in content.splitlines():
        if ':' in line:
            key, val = line.split(':', 1)
            result[key.strip()] = val.strip()
    return result


def _parse_stat(pid: int) -> Optional[Dict[str, Any]]:
    """Parse /proc/[pid]/stat into a dict of fields."""
    content = _read_proc_file(pid, 'stat')
    if not content:
        return None
    
    try:
        # /proc/[pid]/stat format: pid (comm) state ppid pgrp session...
        # Find closing paren after the command name
        first_paren = content.index('(')
        last_paren = content.rindex(')')
        comm = content[first_paren + 1:last_paren]
        rest = content[last_paren + 2:].split()
        
        fields = {
            'pid': int(content[:first_paren].strip()),
            'comm': comm,
            'state': rest[0] if len(rest) > 0 else '?',
            'ppid': int(rest[1]) if len(rest) > 1 else 0,
            'pgrp': int(rest[2]) if len(rest) > 2 else 0,
            'session': int(rest[3]) if len(rest) > 3 else 0,
            'tty_nr': int(rest[4]) if len(rest) > 4 else 0,
            'tty_pgrp': int(rest[5]) if len(rest) > 5 else 0,
            'flags': int(rest[6]) if len(rest) > 6 else 0,
            'min_flt': int(rest[7]) if len(rest) > 7 else 0,
            'cmin_flt': int(rest[8]) if len(rest) > 8 else 0,
            'maj_flt': int(rest[9]) if len(rest) > 9 else 0,
            'cmaj_flt': int(rest[10]) if len(rest) > 10 else 0,
            'utime': int(rest[11]) if len(rest) > 11 else 0,
            'stime': int(rest[12]) if len(rest) > 12 else 0,
            'cutime': int(rest[13]) if len(rest) > 13 else 0,
            'cstime': int(rest[14]) if len(rest) > 14 else 0,
            'priority': int(rest[15]) if len(rest) > 15 else 0,
            'nice': int(rest[16]) if len(rest) > 16 else 0,
            'num_threads': int(rest[17]) if len(rest) > 17 else 0,
            'start_time': int(rest[19]) if len(rest) > 19 else 0,  # jiffies since boot
            'vsize': int(rest[20]) if len(rest) > 20 else 0,  # virtual memory in bytes
            'rss': int(rest[21]) if len(rest) > 21 else 0,  # resident set size in pages
            'rsslim': int(rest[22]) if len(rest) > 22 else 0,
            'processor': int(rest[36]) if len(rest) > 36 else 0,
            'rt_priority': int(rest[37]) if len(rest) > 37 else 0,
            'policy': int(rest[38]) if len(rest) > 38 else 0,
        }
        return fields
    except (ValueError, IndexError) as e:
        logger.debug(f"Failed to parse /proc/{pid}/stat: {e}")
        return None


def _parse_cmdline(pid: int) -> str:
    """Read /proc/[pid]/cmdline (null-separated args → space-joined)."""
    content = _read_proc_file(pid, 'cmdline')
    if content:
        # cmdline uses null bytes as delimiters
        args = content.split('\x00')
        return ' '.join(a for a in args if a)
    # Fallback: read comm from status
    status = _parse_status(pid)
    return status.get('Name', '') if status else '?'


def _get_process_uptime(pid: int, stat_fields: dict) -> float:
    """Calculate process uptime in seconds."""
    start_jiffies = stat_fields.get('start_time', 0)
    if start_jiffies == 0:
        return 0.0
    
    try:
        # Get system uptime from /proc/uptime
        u_time = __import__('time').time()
        uptime_content = __import__('pathlib').Path('/proc/uptime').read_text()
        system_uptime = float(uptime_content.split()[0])
        
        # CLK_TCK is typically 100 on Linux
        try:
            clk_tck = os.sysconf(os.sysconf_names['SC_CLK_TCK'])
        except (AttributeError, ValueError, KeyError):
            clk_tck = 100
        
        process_uptime = system_uptime - (start_jiffies / clk_tck)
        return max(0.0, process_uptime)
    except (FileNotFoundError, ValueError, OSError):
        return 0.0


def _get_memory_info(pid: int, stat_fields: dict) -> Dict[str, float]:
    """Get memory usage for a process in MB and percentage."""
    vsize_bytes = stat_fields.get('vsize', 0)
    rss_pages = stat_fields.get('rss', 0)
    
    try:
        page_size = os.sysconf(os.sysconf_names['SC_PAGE_SIZE'])
    except (AttributeError, ValueError, KeyError):
        page_size = 4096
    
    vsize_mb = vsize_bytes / (1024 * 1024)
    rss_bytes = rss_pages * page_size
    rss_mb = rss_bytes / (1024 * 1024)
    
    # Get total system memory for percentage calculation
    mem_percent = 0.0
    try:
        mem_info = system_memory()
        total_kb = mem_info.get('total_kb', 0)
        if total_kb > 0:
            total_mb = total_kb / 1024
            mem_percent = (rss_mb / total_mb) * 100.0
    except (FileNotFoundError, ValueError, AttributeError, TypeError):
        pass
    
    return {
        'vsize_mb': round(vsize_mb, 1),
        'rss_mb': round(rss_mb, 1),
        'mem_percent': round(mem_percent, 1),
    }


def _get_cpu_percent(pid: int, stat_fields: dict, uptime: float) -> float:
    """Estimate CPU usage percentage for a process."""
    utime = stat_fields.get('utime', 0)
    stime = stat_fields.get('stime', 0)
    total_jiffies = utime + stime
    
    if total_jiffies == 0 or uptime <= 0:
        return 0.0
    
    try:
        clk_tck = os.sysconf(os.sysconf_names['SC_CLK_TCK'])
    except (AttributeError, ValueError, KeyError):
        clk_tck = 100
    
    cpu_seconds = total_jiffies / clk_tck
    # Average CPU usage over the process's lifetime
    cpu_percent = (cpu_seconds / uptime) * 100.0
    return round(min(cpu_percent, 100.0), 1)


def _get_process_user(pid: int, status: dict) -> str:
    """Get the username for a process."""
    uid_str = status.get('Uid', '0').split('\t')[0]
    try:
        uid = int(uid_str)
        return pwd.getpwuid(uid).pw_name
    except (KeyError, ValueError, PermissionError):
        return str(uid_str)


def _get_process_state_display(state_char: str) -> Tuple[str, bool]:
    """Convert state character to human-readable name and check if problematic."""
    state_name = PROCESS_STATES.get(state_char, f'UNKNOWN({state_char})')
    is_zombie = (state_char == 'Z')
    is_problematic = state_char in STUCK_STATES
    return state_name, is_zombie, is_problematic


def _gather_process(pid: int) -> Optional[Dict[str, Any]]:
    """Gather all information about a single process."""
    try:
        stat = _parse_stat(pid)
        if not stat:
            return None
        
        status = _parse_status(pid)
        state_char = stat.get('state', '?')
        state_name, is_zombie, is_problematic = _get_process_state_display(state_char)
        uptime = _get_process_uptime(pid, stat)
        user = _get_process_user(pid, status)
        cmdline = _parse_cmdline(pid)
        memory = _get_memory_info(pid, stat)
        cpu_percent = _get_cpu_percent(pid, stat, uptime)
        
        # Get parent info
        ppid = stat.get('ppid', 0)
        ppid_exists = (PROC_PATH / str(ppid)).is_dir() if ppid > 0 else False
        
        info = {
            'pid': pid,
            'ppid': ppid,
            'ppid_alive': ppid_exists,
            'user': user,
            'state': state_char,
            'state_name': state_name,
            'command': cmdline[:200] if cmdline else '',
            'cpu_percent': cpu_percent,
            'vsize_mb': memory['vsize_mb'],
            'rss_mb': memory['rss_mb'],
            'mem_percent': memory['mem_percent'],
            'uptime_seconds': round(uptime, 1),
            'num_threads': stat.get('num_threads', 0),
            'priority': stat.get('priority', 0),
            'nice': stat.get('nice', 0),
            'processor': stat.get('processor', 0),
            'is_zombie': is_zombie,
            'is_problematic': is_problematic,
            'is_orphan': ppid > 0 and not ppid_exists,
            'is_server_child': _is_server_child(pid, ppid, cmdline, ppid_exists),
            'is_session_child': False,  # Will be set by parent
        }
        return info
    except (ProcessLookupError, FileNotFoundError, PermissionError):
        return None


def _is_server_child(pid: int, ppid: int, cmdline: str, ppid_alive: bool) -> bool:
    """Check if process is a child of the server or a tool it spawned."""
    # Direct child of the server
    if ppid == SERVER_PID:
        return True
    
    # Look up parent chain
    try:
        current_ppid = ppid
        visited = set()
        while current_ppid > 0 and current_ppid not in visited:
            visited.add(current_ppid)
            if current_ppid == SERVER_PID:
                return True
            parent_stat = _parse_stat(current_ppid)
            if not parent_stat:
                break
            current_ppid = parent_stat.get('ppid', 0)
    except Exception:
        pass
    
    return False


def _build_process_tree(processes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Build a hierarchical process tree from flat list."""
    # Index by PID
    by_pid = {p['pid']: p for p in processes}
    
    # Assign children to parents
    for p in processes:
        ppid = p['ppid']
        if ppid in by_pid:
            if 'children' not in by_pid[ppid]:
                by_pid[ppid]['children'] = []
            by_pid[ppid]['children'].append(p)
    
    # Return root processes (those whose parent isn't in the list or are PID 1)
    roots = [p for p in processes if p['ppid'] == 0 or p['ppid'] not in by_pid]
    return roots


def _flatten_process_tree(roots: List[Dict[str, Any]], prefix: str = "", is_last: bool = True) -> List[str]:
    """Flatten a process tree into display lines."""
    lines = []
    for i, proc in enumerate(roots):
        is_last_item = (i == len(roots) - 1)
        connector = "└── " if is_last_item else "├── "
        
        zombie_marker = " 🧟 ZOMBIE" if proc.get('is_zombie') else ""
        orphan_marker = " 👻 ORPHAN" if proc.get('is_orphan') else ""
        stuck_marker = " ⚠️ STUCK" if proc.get('is_problematic') and proc.get('uptime_seconds', 0) > STUCK_MIN_RUNTIME else ""
        
        runtime = proc.get('uptime_seconds', 0)
        runtime_str = _format_duration(runtime)
        
        line = f"{prefix}{connector}PID={proc['pid']} [{proc['state_name']}] {proc['command'][:60]} ({proc['user']}, {runtime_str}, CPU={proc['cpu_percent']}%, MEM={proc['rss_mb']}MB){zombie_marker}{orphan_marker}{stuck_marker}"
        lines.append(line)
        
        if 'children' in proc and proc['children']:
            child_prefix = prefix + ("    " if is_last_item else "│   ")
            lines.extend(_flatten_process_tree(proc['children'], child_prefix))
    
    return lines


def _format_duration(seconds: float) -> str:
    """Format duration in seconds to human-readable string."""
    total_secs = int(seconds)
    if total_secs < 60:
        return f"{total_secs}s"
    elif total_secs < 3600:
        minutes = total_secs // 60
        secs = total_secs % 60
        return f"{minutes}m {secs}s"
    elif total_secs < 86400:
        hours = total_secs // 3600
        minutes = (total_secs % 3600) // 60
        return f"{hours}h {minutes}m"
    else:
        days = total_secs // 86400
        hours = (total_secs % 86400) // 3600
        return f"{days}d {hours}h"


def _classify_process_lingering(proc: Dict[str, Any]) -> Tuple[bool, str]:
    """Check if a process is lingering (should have ended but hasn't)."""
    uptime = proc.get('uptime_seconds', 0)
    command = proc.get('command', '').lower()
    is_zombie = proc.get('is_zombie', False)
    is_orphan = proc.get('is_orphan', False)
    is_problematic = proc.get('is_problematic', False)
    
    # Zombie processes always need cleanup
    if is_zombie:
        return True, "Zombie process needs reaping"
    
    # Orphaned processes that are stuck
    if is_orphan and is_problematic and uptime > STUCK_MIN_RUNTIME:
        return True, "Orphaned stuck process"
    
    # Orphaned processes running too long
    if is_orphan and uptime > LINGERING_DEFAULT_MIN:
        return True, f"Orphaned process running {_format_duration(uptime)}"
    
    # Shell processes running too long
    cmd_base = command.split()[0] if command else ''
    if cmd_base in ('sh', 'bash', 'zsh', 'dash') and uptime > LINGERING_SHELL_MIN:
        return True, f"Shell process running {_format_duration(uptime)} (expected < 5min)"
    
    # Python scripts running too long
    if 'python' in cmd_base and uptime > LINGERING_PYTHON_MIN:
        return True, f"Python process running {_format_duration(uptime)} (expected < 10min)"
    
    # Sleep commands running for suspiciously long
    if cmd_base == 'sleep' and uptime > 3600:
        return True, f"Sleep process running {_format_duration(uptime)} (suspicious)"
    
    # Problematic state + long runtime
    if is_problematic and uptime > STUCK_MIN_RUNTIME * 6:
        return True, f"In state '{proc.get('state_name', '?')}' for {_format_duration(uptime)}"
    
    return False, ""


# ---------------------------------------------------------------------------
# Main tool function
# ---------------------------------------------------------------------------
def process_monitor(
    filter_user: Optional[str] = None,
    filter_state: Optional[str] = None,
    filter_name: Optional[str] = None,
    show_zombies_only: bool = False,
    show_problematic_only: bool = False,
    show_orphans_only: bool = False,
    show_server_children: bool = False,
    show_lingering_only: bool = False,
    show_tree: bool = False,
    sort_by: str = "pid",
    max_results: int = 200,
    format_output: str = "text",
    progress_callback: Optional[Any] = None,
) -> str:
    """
    Monitor and inspect running OS processes with advanced diagnostics.

    Provides comprehensive process visibility: lists all processes with
    PID, PPID, user, state, CPU/memory usage, and runtime. Detects
    zombie processes, orphaned processes, stuck/long-running processes,
    and server-child processes. Can display process hierarchy trees.

    **When to use:** Use to inspect what processes are running on the system,
    find zombie/lingering processes, check resource usage, or identify
    processes spawned by the FastMCP server.

    Args:
        filter_user: Show only processes for this username (e.g., 'deck').
        filter_state: Show only processes in this state (e.g., 'Z' for zombie, 'R' for running).
        filter_name: Show only processes whose command line contains this substring.
        show_zombies_only: Show only zombie (defunct) processes.
        show_problematic_only: Show only problematic processes (zombie, stopped, stuck).
        show_orphans_only: Show only orphaned processes (parent no longer exists).
        show_server_children: Show only processes related to the FastMCP server.
        show_lingering_only: Show only processes that appear to be lingering (should have ended).
        show_tree: Display processes in a hierarchical tree view.
        sort_by: Sort field: 'pid', 'cpu', 'mem', 'uptime', 'state', 'user'.
        max_results: Maximum number of processes to return (default 200, max 1000).
        format_output: Output format: 'text' (formatted table) or 'json' (raw data).
        progress_callback: Optional progress callback.

    Returns:
        Comprehensive process report with diagnostics, or JSON data if format_output='json'.
    """
    if progress_callback:
        progress_callback(1)
    
    # Validate parameters
    max_results = min(max_results, 1000)
    
    # Scan all processes
    all_pids = []
    try:
        for entry in PROC_PATH.iterdir():
            if entry.name.isdigit():
                all_pids.append(int(entry.name))
    except PermissionError as e:
        return f"Error: Cannot access /proc - {e}"
    
    if progress_callback:
        progress_callback(2)
    
    # Gather process info
    processes = []
    for pid in all_pids:
        try:
            info = _gather_process(pid)
            if info:
                processes.append(info)
        except Exception as e:
            logger.debug(f"Error gathering process {pid}: {e}")
    
    if progress_callback:
        progress_callback(3)
    
    # -- Apply filters --
    filtered = processes
    
    if filter_user:
        filtered = [p for p in filtered if p['user'] == filter_user]
    if filter_state:
        filtered = [p for p in filtered if p['state'].upper() == filter_state.upper() or 
                    p['state_name'].upper() == filter_state.upper()]
    if filter_name:
        filter_lower = filter_name.lower()
        filtered = [p for p in filtered if filter_lower in p['command'].lower()]
    if show_zombies_only:
        filtered = [p for p in filtered if p['is_zombie']]
    if show_problematic_only:
        filtered = [p for p in filtered if p['is_problematic'] or p['is_zombie']]
    if show_orphans_only:
        filtered = [p for p in filtered if p['is_orphan']]
    if show_server_children:
        filtered = [p for p in filtered if p['is_server_child']]
    if show_lingering_only:
        filtered_list = []
        for p in filtered:
            is_lingering, reason = _classify_process_lingering(p)
            if is_lingering:
                p['_lingering_reason'] = reason
                filtered_list.append(p)
        filtered = filtered_list
    
    # -- Sort --
    sort_key_map = {
        'pid': lambda p: p['pid'],
        'cpu': lambda p: p['cpu_percent'],
        'mem': lambda p: p['rss_mb'],
        'uptime': lambda p: p['uptime_seconds'],
        'state': lambda p: p['state'],
        'user': lambda p: p['user'],
    }
    sort_func = sort_key_map.get(sort_by.lower(), sort_key_map['pid'])
    filtered.sort(key=sort_func, reverse=(sort_by in ('cpu', 'mem', 'uptime')))
    
    # -- Limit results --
    total_count = len(filtered)
    filtered = filtered[:max_results]
    
    if progress_callback:
        progress_callback(4)
    
    # -- Build output --
    if format_output == 'json':
        return _format_json_output(filtered, total_count)
    
    return _format_text_output(
        filtered, total_count, show_tree, show_lingering_only,
        filter_user, filter_state, filter_name,
        show_zombies_only, show_problematic_only, show_orphans_only,
        show_server_children
    )


def _format_text_output(processes, total_count, show_tree, show_lingering_only,
                        filter_user, filter_state, filter_name,
                        show_zombies_only, show_problematic_only,
                        show_orphans_only, show_server_children):
    """Format process information as human-readable text."""
    lines = []
    
    # -- Header --
    lines.append("=" * 100)
    lines.append("🔍 PROCESS MONITOR — System Process Status")
    lines.append("=" * 100)
    lines.append(f"Server PID: {SERVER_PID}")
    lines.append(f"Total processes: {total_count}")
    
    # Active filters
    filters = []
    if filter_user: filters.append(f"user={filter_user}")
    if filter_state: filters.append(f"state={filter_state}")
    if filter_name: filters.append(f"name={filter_name}")
    if show_zombies_only: filters.append("zombies_only")
    if show_problematic_only: filters.append("problematic_only")
    if show_orphans_only: filters.append("orphans_only")
    if show_server_children: filters.append("server_children")
    if show_lingering_only: filters.append("lingering_only")
    if filters:
        lines.append(f"Filters: {', '.join(filters)}")
    else:
        lines.append("Filters: (none — showing all processes)")
    lines.append("")
    
    if not processes:
        lines.append("No processes match your criteria.")
        return "\n".join(lines)
    
    # -- Summary section --
    zombies = [p for p in processes if p['is_zombie']]
    orphans = [p for p in processes if p['is_orphan']]
    problematic = [p for p in processes if p['is_problematic']]
    server_children = [p for p in processes if p['is_server_child']]
    lingering = [p for p in processes if _classify_process_lingering(p)[0]]
    
    lines.append("📊 Summary:")
    lines.append(f"  • {len(zombies)} zombie process(es) {'🧟' if zombies else '✅'}")
    lines.append(f"  • {len(orphans)} orphaned process(es) {'👻' if orphans else '✅'}")
    lines.append(f"  • {len(problematic)} problematic process(es) {'⚠️' if problematic else '✅'}")
    lines.append(f"  • {len(server_children)} server-child process(es)")
    lines.append(f"  • {len(lingering)} lingering/suspicious process(es) {'🚩' if lingering else '✅'}")
    lines.append("")
    
    # -- Lingering analysis (always shown when detected) --
    if lingering:
        lines.append("🚩 LINGERING PROCESSES (should have ended):")
        lines.append("-" * 100)
        for p in processes:
            is_ling, reason = _classify_process_lingering(p)
            if is_ling:
                runtime = _format_duration(p['uptime_seconds'])
                lines.append(f"  PID={p['pid']:<6} {p['command'][:60]:<62} [{p['state_name']:<15}] {runtime:<10} {reason}")
        lines.append("")
    
    # -- Zombie processes --
    if zombies:
        lines.append("🧟 ZOMBIE PROCESSES:")
        lines.append("-" * 100)
        for p in zombies:
            runtime = _format_duration(p['uptime_seconds'])
            lines.append(f"  PID={p['pid']:<6} PPID={p['ppid']:<6} {p['command'][:60]:<62} {runtime:<10} Zombie (needs reaping)")
        lines.append("")
    
    # -- Server children --
    if server_children:
        lines.append("🔧 SERVER CHILD PROCESSES:")
        lines.append("-" * 100)
        for p in server_children[:20]:  # Limit to 20
            runtime = _format_duration(p['uptime_seconds'])
            state_icon = "🧟" if p['is_zombie'] else "👻" if p['is_orphan'] else "⚠️" if p['is_problematic'] else "✅"
            lines.append(f"  PID={p['pid']:<6} {p['command'][:60]:<62} {state_icon} [{p['state_name']:<15}] {runtime:<10} CPU={p['cpu_percent']}% MEM={p['rss_mb']}MB")
        if len(server_children) > 20:
            lines.append(f"  ... and {len(server_children) - 20} more server-child processes")
        lines.append("")
    
    # -- Tree or Table --
    if show_tree:
        lines.append("🌳 PROCESS TREE:")
        lines.append("-" * 100)
        roots = _build_process_tree(processes)
        tree_lines = _flatten_process_tree(roots)
        lines.extend(tree_lines)
    else:
        lines.append("📋 PROCESS TABLE:")
        lines.append("-" * 100)
        header = f"{'PID':<7} {'PPID':<7} {'USER':<10} {'STATE':<16} {'CPU%':<6} {'MEM(MB)':<9} {'RUNTIME':<10} {'THR':<4} COMMAND"
        lines.append(header)
        lines.append("-" * 100)
        
        for p in processes:
            runtime = _format_duration(p['uptime_seconds'])
            marker = ""
            if p['is_zombie']:
                marker = " 🧟"
            elif p['is_orphan']:
                marker = " 👻"
            elif p['is_problematic']:
                marker = " ⚠️"
            
            line = f"{p['pid']:<7} {p['ppid']:<7} {p['user']:<10} {p['state_name']:<16} {p['cpu_percent']:<6} {p['rss_mb']:<9} {runtime:<10} {p['num_threads']:<4} {p['command'][:60]}{marker}"
            lines.append(line)
    
    lines.append("")
    lines.append("=" * 100)
    lines.append(f"Report generated — {len(processes)} processes shown (of {total_count} total matching)")
    lines.append("💡 Tip: Use process_cleanup(kill_zombies=True) to clean zombie processes")
    lines.append("      or process_cleanup(pids=[...], force=True) to kill specific processes")
    lines.append("=" * 100)
    
    return "\n".join(lines)


def _format_json_output(processes, total_count):
    """Format process information as JSON."""
    # Add lingering analysis to each process
    for p in processes:
        is_ling, reason = _classify_process_lingering(p)
        p['is_lingering'] = is_ling
        p['lingering_reason'] = reason
    
    output = {
        'server_pid': SERVER_PID,
        'total_matching': total_count,
        'processes_shown': len(processes),
        'summary': {
            'zombies': len([p for p in processes if p['is_zombie']]),
            'orphans': len([p for p in processes if p['is_orphan']]),
            'problematic': len([p for p in processes if p['is_problematic']]),
            'server_children': len([p for p in processes if p['is_server_child']]),
            'lingering': len([p for p in processes if p.get('is_lingering', False)]),
        },
        'processes': processes,
    }
    
    return json.dumps(output, indent=2, default=str)
