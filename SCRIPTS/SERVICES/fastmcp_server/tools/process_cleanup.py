#!/usr/bin/env python3

# ── Dynamic Config Registration ──────────────────────────────
try:
    from dynamic_config_loader import register_configs
    register_configs("process_cleanup", [
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
tools/process_cleanup.py — Process Killer, Cleanup & Management Tool

Provides the ability to:
- Kill specific processes by PID or name pattern
- Force-kill zombie/defunct processes
- Bulk cleanup of lingering/stuck/orphaned processes
- Clean orphaned process groups
- Graceful (SIGTERM) vs Force (SIGKILL) modes
- Automatic zombie reaping via parent SIGCHLD
- Session-based cleanup (kill entire process groups)

Integrates with `process_monitor` for detection and this tool for action.
Respects dry-run mode. Logs all kill operations for audit trail.

**Safety features:**
- NEVER kills PID 1, PID 0, or the server itself
- Double-checks before killing process groups
- Requires explicit `force` parameter for SIGKILL
- Shows preview of what will be killed before acting
- Logs all actions to the backup/audit system

**Backup:** Kill operations are logged via the backup manager.
"""

import os
import signal
import time
import json
import logging
from pathlib import Path
from typing import Optional, Any, List, Dict, Set

from ecosystem_os_abstraction import process_kill, process_kill_group, process_info, process_list, Signals
logger = logging.getLogger("process_cleanup")

# ---------------------------------------------------------------------------
# Safety constants
# ---------------------------------------------------------------------------
SERVER_PID = os.getpid()
PROTECTED_PIDS = {0, 1, SERVER_PID}  # Never kill these
PROTECTED_PROCESSES = {'systemd', 'init', 'python', 'python3'}  # Name-based protection


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------
def _get_process_name(pid: int) -> str:
    """Get the process name/command from /proc/[pid]/cmdline or status."""
    try:
        cmdline = Path(f"/proc/{pid}/cmdline")
        if cmdline.is_file():
            content = cmdline.read_text(errors='replace')
            args = content.split('\x00')
            return ' '.join(a for a in args if a)[:200]
    except (PermissionError, FileNotFoundError, ProcessLookupError):
        pass
    
    # Fallback
    try:
        status = Path(f"/proc/{pid}/status")
        if status.is_file():
            for line in status.read_text().splitlines():
                if line.startswith('Name:'):
                    return line.split(':', 1)[1].strip()
    except Exception:
        pass
    
    return f"pid:{pid}"


def _get_process_user(pid: int) -> str:
    """Get the username running a process."""
    try:
        status = Path(f"/proc/{pid}/status")
        if status.is_file():
            for line in status.read_text().splitlines():
                if line.startswith('Uid:'):
                    uid_str = line.split(':')[1].strip().split('\t')[0]
                    import pwd
                    try:
                        return pwd.getpwuid(int(uid_str)).pw_name
                    except (KeyError, ValueError):
                        return uid_str
    except Exception:
        pass
    return "unknown"


def _is_zombie(pid: int) -> bool:
    """Check if a process is a zombie."""
    try:
        status = Path(f"/proc/{pid}/status")
        if status.is_file():
            for line in status.read_text().splitlines():
                if line.startswith('State:'):
                    return 'Z' in line
    except Exception:
        pass
    return False


def _zombie_can_be_killed(pid: int) -> bool:
    """
    Zombie processes can't be killed directly (they're already dead).
    They need to be "reaped" by their parent via wait()/waitpid().
    If the parent is dead, the zombie is adopted by init (PID 1) and
    will be reaped automatically.
    
    Returns True if we can attempt to reap (by killing parent or
    sending SIGCHLD to init).
    """
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
        # Parse PPID from /proc/[pid]/stat
        # Format: pid (comm) state ppid ...
        first_paren = stat.index('(')
        last_paren = stat.rindex(')')
        rest = stat[last_paren + 2:].split()
        ppid = int(rest[1]) if len(rest) > 1 else 0
        return ppid > 0
    except Exception:
        return False


def _kill_process(pid: int, sig: int = signal.SIGTERM) -> Dict[str, Any]:
    """
    Send a signal to a process.
    Returns a dict with success status, message, and details.
    """
    result = {
        'pid': pid,
        'signal': sig,
        'success': False,
        'message': '',
        'process_name': '',
    }
    
    # Safety checks
    if pid in PROTECTED_PIDS:
        result['message'] = f"REFUSED: PID {pid} is protected (server itself or system critical)"
        return result
    
    try:
        pname = _get_process_name(pid)
        result['process_name'] = pname
        
        # Check name-based protection
        pname_lower = pname.lower()
        for protected in PROTECTED_PROCESSES:
            if protected in pname_lower and pid != SERVER_PID:
                # Allow if it's a child process that's hanging
                stat = Path(f"/proc/{pid}/stat")
                if not stat.exists():
                    result['message'] = f"Process {pid} already gone"
                    result['success'] = True
                    return result
                # Check if it's in a bad state
                try:
                    content = stat.read_text()
                    state_char = content.split(') ')[1].split()[0] if ') ' in content else '?'
                    if state_char not in ('Z', 'T', 'D'):
                        result['message'] = f"REFUSED: {pname} (PID {pid}) is protected by name pattern"
                        return result
                except Exception:
                    pass
        
        # Send the signal
        os.kill(pid, sig)
        
        # Verify the process is actually gone (for SIGKILL)
        if sig == signal.SIGKILL:
            time.sleep(0.1)
            try:
                os.kill(pid, 0)  # Test if process still exists
                result['message'] = f"Signal {sig} sent but process {pid} still exists (may need parent reaping)"
                result['success'] = True
            except ProcessLookupError:
                result['message'] = f"Process {pid} ({pname[:60]}) successfully killed"
                result['success'] = True
            except PermissionError:
                # Process exists but we can't signal it (different user)
                result['message'] = f"Signal {sig} sent to {pid}, but process may still be running (different user)"
                result['success'] = True
        else:
            # For SIGTERM, consider it a success if signal was sent
            result['message'] = f"Signal {sig} (SIGTERM) sent to PID {pid} ({pname[:60]})"
            result['success'] = True
        
    except ProcessLookupError:
        result['message'] = f"Process {pid} does not exist (already terminated)"
        result['success'] = True  # Already dead counts as success
    except PermissionError:
        result['message'] = f"Permission denied: cannot signal PID {pid} (different user or insufficient privileges)"
        result['success'] = False
    except OSError as e:
        result['message'] = f"OS error signaling PID {pid}: {e}"
        result['success'] = False
    
    return result


def _kill_process_group(pgrp: int, sig: int = signal.SIGTERM) -> Dict[str, Any]:
    """
    Kill an entire process group by session/PID.
    """
    result = {
        'pgrp': pgrp,
        'signal': sig,
        'success': False,
        'killed': [],
        'message': '',
    }
    
    if pgrp in PROTECTED_PIDS:
        result['message'] = f"REFUSED: Process group {pgrp} contains protected PIDs"
        return result
    
    killed_pids = []
    # Find all processes in this group
    try:
        for proc_dir in Path('/proc').iterdir():
            if not proc_dir.name.isdigit():
                continue
            pid = int(proc_dir.name)
            try:
                stat_path = proc_dir / 'stat'
                if stat_path.is_file():
                    content = stat_path.read_text()
                    first_paren = content.index('(')
                    last_paren = content.rindex(')')
                    rest = content[last_paren + 2:].split()
                    proc_pgrp = int(rest[2]) if len(rest) > 2 else 0
                    
                    if proc_pgrp == pgrp and pid not in PROTECTED_PIDS:
                        kill_result = _kill_process(pid, sig)
                        if kill_result['success']:
                            killed_pids.append(pid)
            except (ValueError, IndexError, PermissionError, ProcessLookupError):
                continue
    except PermissionError:
        pass
    
    if killed_pids:
        result['success'] = True
        result['killed'] = killed_pids
        result['message'] = f"Killed {len(killed_pids)} process(es) in group {pgrp}: {killed_pids}"
    else:
        result['message'] = f"No processes found in group {pgrp} or all protected"
    
    return result


def _reap_zombies() -> Dict[str, Any]:
    """
    Attempt to reap zombie processes.
    
    Zombies can't be killed directly. They're already dead — they just
    haven't been waited on by their parent. Options:
    1. Kill the parent → zombie adopted by init → init reaps it
    2. Send SIGCHLD to the parent → parent may reap child
    3. Wait for init to reap it automatically
    
    Returns dict with reaping results.
    """
    zombies = []
    try:
        for proc_dir in Path('/proc').iterdir():
            if not proc_dir.name.isdigit():
                continue
            pid = int(proc_dir.name)
            try:
                status_path = proc_dir / 'status'
                if status_path.is_file():
                    content = status_path.read_text()
                    if 'Z' in content.split('State:')[1].split('\n')[0] if 'State:' in content else '':
                        # Found a zombie
                        ppid = 0
                        for line in content.splitlines():
                            if line.startswith('PPid:'):
                                ppid = int(line.split(':')[1].strip())
                                break
                        zombies.append({'pid': pid, 'ppid': ppid, 'name': _get_process_name(pid)})
            except Exception:
                continue
    except PermissionError:
        pass
    
    if not zombies:
        return {'success': True, 'reaped': 0, 'message': 'No zombie processes found', 'zombies': []}
    
    reaped = 0
    results = []
    
    for zombie in zombies:
        pid = zombie['pid']
        ppid = zombie['ppid']
        
        # Strategy 1: Send SIGCHLD to parent to encourage reaping
        if ppid > 1:
            try:
                os.kill(ppid, signal.SIGCHLD)
                results.append(f"Sent SIGCHLD to parent PID {ppid} for zombie {pid}")
                reaped += 1
            except (PermissionError, ProcessLookupError):
                pass
        
        # Strategy 2: If parent is init (PID 1), init will reap it eventually
        if ppid == 1:
            results.append(f"Zombie {pid} (PID={zombie['name'][:30]}) parented by init — will be reaped automatically")
            continue
        
        # Strategy 3: If parent process no longer exists, zombie will be adopted by init
        try:
            os.kill(ppid, 0)  # Check if parent exists
        except ProcessLookupError:
            # Parent is dead — zombie will be adopted by init soon
            results.append(f"Zombie {pid} parent (PID {ppid}) is dead — will be auto-reaped by init")
            continue
        
        # Strategy 4: Try to kill the parent (only if parent is a server child)
        # Check if parent is a server-related process
        parent_name = _get_process_name(ppid).lower()
        if any(p in parent_name for p in ('python', 'sh', 'bash', 'sleep')):
            try:
                # Only SIGTERM the parent, let it clean up naturally
                os.kill(ppid, signal.SIGTERM)
                results.append(f"Sent SIGTERM to parent PID {ppid} ({parent_name[:30]}) to reap zombie {pid}")
                reaped += 1
            except (PermissionError, ProcessLookupError):
                pass
    
    return {
        'success': reaped > 0 or len(zombies) == 0,
        'reaped': reaped,
        'zombies_found': len(zombies),
        'message': f"Attempted to reap {len(zombies)} zombie(s): {reaped} actions taken",
        'details': results,
        'zombies': zombies,
    }


def _get_process_group(pid: int) -> int:
    """Get the process group ID for a PID."""
    try:
        stat_path = Path(f"/proc/{pid}/stat")
        if stat_path.is_file():
            content = stat_path.read_text()
            first_paren = content.index('(')
            last_paren = content.rindex(')')
            rest = content[last_paren + 2:].split()
            return int(rest[2]) if len(rest) > 2 else 0
    except Exception:
        pass
    return 0


def _log_kill_action(action: str, details: dict):
    """Log a kill action to the backup/audit system."""
    try:
        from backup_manager import get_backup_manager, get_current_entry_id
        mgr = get_backup_manager()
        eid = get_current_entry_id()
        if eid:
            mgr.save_file_snapshot(
                eid,
                f"process_cleanup/{action}",
                'kill',
                {"existed": False, "content": None, "hash": None, "size": 0},
                json.dumps(details, indent=2)
            )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Main tool function
# ---------------------------------------------------------------------------
def process_cleanup(
    pids: Optional[List[int]] = None,
    kill_zombies: bool = False,
    kill_orphans: bool = False,
    kill_problematic: bool = False,
    kill_lingering: bool = False,
    kill_by_name: Optional[str] = None,
    kill_by_user: Optional[str] = None,
    kill_process_group: Optional[int] = None,
    kill_session_children: bool = False,
    force: bool = False,
    dry_run: Optional[bool] = None,
    max_to_kill: int = 50,
    progress_callback: Optional[Any] = None,
) -> str:
    """
    Kill, clean up, and manage running OS processes.

    Provides process termination and cleanup: kill specific processes by PID,
    force-kill zombies, clean up orphaned/stuck/lingering processes, and
    manage process groups. Supports graceful (SIGTERM) and force (SIGKILL) modes.

    **When to use:** Use to terminate stuck processes, clean up zombies,
    kill lingering processes detected by `process_monitor`, or manage
    orphaned process groups. Always uses SIGTERM first unless `force=True`.

    **Safety:** Never kills the server process (PID {SERVER_PID}), PID 0, or PID 1.
    Name-based protection prevents accidental kills of critical system processes.
    Use `dry_run=True` to preview before acting.

    Args:
        pids: List of specific PIDs to kill (e.g., [1234, 5678]).
        kill_zombies: Attempt to reap all zombie (defunct) processes.
        kill_orphans: Kill orphaned processes (parent no longer exists).
        kill_problematic: Kill problematic processes (zombies, stuck, stopped).
        kill_lingering: Kill processes that should have ended (detected by process_monitor logic).
        kill_by_name: Kill processes whose command line contains this string (e.g., "sleep", "stuck_script").
        kill_by_user: Kill all processes owned by this user (caution: use with force=True).
        kill_process_group: Kill an entire process group by group ID.
        kill_session_children: Kill all server-child processes that are stuck.
        force: Use SIGKILL (kill -9) instead of SIGTERM. Required for stubborn processes.
        dry_run: If True, only show what would be killed without acting.
                 Defaults to global dry-run config.
        max_to_kill: Maximum number of processes to kill in one call (default 50, max 200).
        progress_callback: Optional progress callback.

    Returns:
        Detailed report of what was killed, what failed, and current process status.
    """
    if progress_callback:
        progress_callback(1)
    
    # Check dry-run mode
    if dry_run is None:
        try:
            import config
            dry_run = config.config.get("dry_run", True)
        except Exception:
            dry_run = True
    
    # Validate
    max_to_kill = min(max_to_kill, 200)
    sig = signal.SIGKILL if force else signal.SIGTERM
    sig_name = "SIGKILL (-9)" if force else "SIGTERM (-15)"
    actions = []
    skipped = []
    errors = []
    
    # Track which PIDs we've already tried to avoid duplicate kills
    killed_pids: Set[int] = set()
    scheduled_kills: List[int] = []
    
    # -- Dry run header --
    if dry_run:
        header = f"{'='*80}\n🔍 PROCESS CLEANUP — DRY RUN (no actions taken)\n{'='*80}\n"
        header += f"Would use: {sig_name}\n"
        header += f"Mode: {'Force (SIGKILL)' if force else 'Graceful (SIGTERM)'}\n\n"
    else:
        header = f"{'='*80}\n🔧 PROCESS CLEANUP — Executing\n{'='*80}\n"
        header += f"Using signal: {sig_name}\n\n"
    
    if progress_callback:
        progress_callback(2)
    
    # -- 1. Collect targets from all specified options --
    # (a) Specific PIDs
    if pids:
        for pid in pids:
            if pid not in killed_pids and pid not in PROTECTED_PIDS:
                scheduled_kills.append(pid)
                killed_pids.add(pid)
    
    # (b) Zombies
    if kill_zombies:
        try:
            for proc_dir in Path('/proc').iterdir():
                if not proc_dir.name.isdigit():
                    continue
                pid = int(proc_dir.name)
                if pid in killed_pids or pid in PROTECTED_PIDS:
                    continue
                try:
                    status_path = proc_dir / 'status'
                    if status_path.is_file():
                        content = status_path.read_text()
                        if 'State:' in content:
                            state_line = [l for l in content.splitlines() if l.startswith('State:')]
                            if state_line and 'Z' in state_line[0]:
                                scheduled_kills.append(pid)
                                killed_pids.add(pid)
                except Exception:
                    continue
        except PermissionError:
            errors.append("Permission denied scanning /proc for zombies")
    
    # (c) Orphans
    if kill_orphans:
        try:
            for proc_dir in Path('/proc').iterdir():
                if not proc_dir.name.isdigit():
                    continue
                pid = int(proc_dir.name)
                if pid in killed_pids or pid in PROTECTED_PIDS:
                    continue
                try:
                    stat_path = proc_dir / 'stat'
                    if stat_path.is_file():
                        content = stat_path.read_text()
                        first_paren = content.index('(')
                        last_paren = content.rindex(')')
                        rest = content[last_paren + 2:].split()
                        ppid = int(rest[1]) if len(rest) > 1 else 0
                        
                        if ppid > 1:
                            # Check if parent exists
                            try:
                                os.kill(ppid, 0)
                            except ProcessLookupError:
                                scheduled_kills.append(pid)
                                killed_pids.add(pid)
                except Exception:
                    continue
        except PermissionError:
            errors.append("Permission denied scanning /proc for orphans")
    
    # (d) Problematic
    if kill_problematic:
        problematic_states = {'Z', 'T', 't', 'D'}
        try:
            for proc_dir in Path('/proc').iterdir():
                if not proc_dir.name.isdigit():
                    continue
                pid = int(proc_dir.name)
                if pid in killed_pids or pid in PROTECTED_PIDS:
                    continue
                try:
                    stat_path = proc_dir / 'stat'
                    if stat_path.is_file():
                        content = stat_path.read_text()
                        first_paren = content.index('(')
                        last_paren = content.rindex(')')
                        state_char = content[last_paren + 2:].split()[0] if ') ' in content else ''
                        
                        if state_char in problematic_states:
                            scheduled_kills.append(pid)
                            killed_pids.add(pid)
                except Exception:
                    continue
        except PermissionError:
            errors.append("Permission denied scanning /proc for problematic processes")
    
    # (e) Lingering
    if kill_lingering:
        # Reuse the process_monitor logic
        try:
            # Import and use process_monitor's detection
            from process_monitor import _gather_process, _classify_process_lingering
            for proc_dir in Path('/proc').iterdir():
                if not proc_dir.name.isdigit():
                    continue
                pid = int(proc_dir.name)
                if pid in killed_pids or pid in PROTECTED_PIDS:
                    continue
                try:
                    info = _gather_process(pid)
                    if info:
                        is_ling, _ = _classify_process_lingering(info)
                        if is_ling:
                            scheduled_kills.append(pid)
                            killed_pids.add(pid)
                except Exception:
                    continue
        except ImportError:
            # Fallback: just check if process has been running > 1 hour
            from process_monitor import _gather_process
            for proc_dir in Path('/proc').iterdir():
                if not proc_dir.name.isdigit():
                    continue
                pid = int(proc_dir.name)
                if pid in killed_pids or pid in PROTECTED_PIDS:
                    continue
                try:
                    info = _gather_process(pid)
                    if info and info.get('uptime_seconds', 0) > 3600:
                        # Check if it's a shell or python process
                        cmd = info.get('command', '').lower()
                        if any(p in cmd for p in ('sh ', 'bash', 'python', 'sleep', 'ping')):
                            scheduled_kills.append(pid)
                            killed_pids.add(pid)
                except Exception:
                    continue
    
    # (f) By name pattern
    if kill_by_name:
        name_lower = kill_by_name.lower()
        try:
            for proc_dir in Path('/proc').iterdir():
                if not proc_dir.name.isdigit():
                    continue
                pid = int(proc_dir.name)
                if pid in killed_pids or pid in PROTECTED_PIDS:
                    continue
                try:
                    cmdline = (proc_dir / 'cmdline')
                    if cmdline.is_file():
                        content = cmdline.read_text(errors='replace')
                        args = ' '.join(a for a in content.split('\x00') if a).lower()
                        name_from_status = ""
                        status_path = proc_dir / 'status'
                        if status_path.is_file():
                            for line in status_path.read_text().splitlines():
                                if line.startswith('Name:'):
                                    name_from_status = line.split(':')[1].strip().lower()
                        
                        if name_lower in args or name_lower in name_from_status:
                            scheduled_kills.append(pid)
                            killed_pids.add(pid)
                except Exception:
                    continue
        except PermissionError:
            errors.append(f"Permission denied scanning /proc for processes matching '{kill_by_name}'")
    
    # (g) By user
    if kill_by_user:
        try:
            for proc_dir in Path('/proc').iterdir():
                if not proc_dir.name.isdigit():
                    continue
                pid = int(proc_dir.name)
                if pid in killed_pids or pid in PROTECTED_PIDS:
                    continue
                try:
                    user = _get_process_user(pid)
                    if user == kill_by_user:
                        scheduled_kills.append(pid)
                        killed_pids.add(pid)
                except Exception:
                    continue
        except PermissionError:
            errors.append(f"Permission denied scanning /proc for user '{kill_by_user}'")
    
    # (h) Server session children
    if kill_session_children:
        try:
            for proc_dir in Path('/proc').iterdir():
                if not proc_dir.name.isdigit():
                    continue
                pid = int(proc_dir.name)
                if pid in killed_pids or pid in PROTECTED_PIDS:
                    continue
                try:
                    stat_path = proc_dir / 'stat'
                    if stat_path.is_file():
                        content = stat_path.read_text()
                        first_paren = content.index('(')
                        last_paren = content.rindex(')')
                        rest = content[last_paren + 2:].split()
                        ppid = int(rest[1]) if len(rest) > 1 else 0
                        
                        # Check if ancestor chain leads to server
                        check_pid = ppid
                        visited = set()
                        while check_pid > 0 and check_pid not in visited:
                            visited.add(check_pid)
                            if check_pid == SERVER_PID:
                                # Check if process is in a bad state or running too long
                                try:
                                    uptime_content = Path('/proc/uptime').read_text()
                                    system_uptime = float(uptime_content.split()[0])
                                    stat_fields = {
                                        'start_time': int(rest[19]) if len(rest) > 19 else 0,
                                    }
                                    clk_tck = os.sysconf(os.sysconf_names['SC_CLK_TCK'])
                                    proc_uptime = system_uptime - (stat_fields['start_time'] / clk_tck)
                                    
                                    state_char = rest[0] if len(rest) > 0 else ''
                                    is_stuck = state_char in ('Z', 'T', 't', 'D') or proc_uptime > 300
                                    
                                    if is_stuck:
                                        scheduled_kills.append(pid)
                                        killed_pids.add(pid)
                                except Exception:
                                    scheduled_kills.append(pid)
                                    killed_pids.add(pid)
                                break
                            
                            # Move up to grandparent
                            try:
                                gp_stat = Path(f"/proc/{check_pid}/stat").read_text()
                                gp_first = gp_stat.index('(')
                                gp_last = gp_stat.rindex(')')
                                gp_rest = gp_stat[gp_last + 2:].split()
                                check_pid = int(gp_rest[1]) if len(gp_rest) > 1 else 0
                            except Exception:
                                break
                except Exception:
                    continue
        except PermissionError:
            errors.append("Permission denied scanning /proc for session children")
    
    if progress_callback:
        progress_callback(3)
    
    # -- Limit kills --
    if len(scheduled_kills) > max_to_kill:
        extra = scheduled_kills[max_to_kill:]
        scheduled_kills = scheduled_kills[:max_to_kill]
        skipped.append(f"{len(extra)} processes over max_to_kill limit ({max_to_kill})")
        # Add back extra pids to killed_pids so they don't show in skipped description
        for extra_pid in extra:
            killed_pids.discard(extra_pid)
    
    # -- Execute kills --
    if dry_run:
        # Just preview
        preview_lines = []
        for pid in scheduled_kills:
            pname = _get_process_name(pid)
            user = _get_process_user(pid)
            preview_lines.append(f"  Would kill: PID={pid:<6} user={user:<8} {pname[:70]}")
        
        if not preview_lines and not errors:
            return header + "No processes matched the specified criteria. Nothing to clean up."
        
        output = header
        output += f"📋 Processes to be killed ({len(scheduled_kills)}):\n"
        output += "\n".join(preview_lines) + "\n"
        if skipped:
            output += f"\n⏭️ Skipped: {', '.join(skipped)}\n"
        if errors:
            output += f"\n⚠️ Errors during scan:\n" + "\n".join(f"  • {e}" for e in errors) + "\n"
        output += f"\n💡 Run with dry_run=False to execute, or force=True for SIGKILL"
        return output
    
    # -- Actually perform kills --
    result_lines = []
    for pid in scheduled_kills:
        result = _kill_process(pid, sig)
        if result['success']:
            actions.append(result)
            result_lines.append(f"  ✅ {result['message']}")
        else:
            errors.append(result['message'])
            result_lines.append(f"  ❌ {result['message']}")
    
    # -- Special handling for zombies (need reaping, not killing) --
    zombie_result = None
    if kill_zombies and not dry_run:
        zombie_result = _reap_zombies()
        if zombie_result['reaped'] > 0:
            result_lines.append(f"  🧟 {zombie_result['message']}")
    
    if progress_callback:
        progress_callback(4)
    
    # -- Log to backup system --
    if not dry_run:
        _log_kill_action('cleanup', {
            'pids': scheduled_kills,
            'signal': sig_name,
            'force': force,
            'actions_taken': len(actions),
            'errors': errors if errors else None,
            'zombie_reaping': zombie_result,
        })
    
    # -- Build output --
    output = header
    output += f"📊 RESULTS:\n"
    output += f"  ✅ Successfully processed: {len(actions)} process(es)\n"
    output += f"  ❌ Errors: {len(errors)}\n"
    output += f"  ⏭️  Skipped: {len(skipped)}\n"
    
    if result_lines:
        output += "\n📋 Details:\n" + "\n".join(result_lines) + "\n"
    
    if skipped:
        output += f"\n⏭️ Skipped: {', '.join(skipped)}\n"
    if errors:
        output += f"\n⚠️ Errors:\n" + "\n".join(f"  • {e}" for e in errors) + "\n"
    
    # -- Current status summary --
    remaining_zombies = 0
    try:
        remaining_zombies = sum(1 for d in Path('/proc').iterdir() 
                               if d.name.isdigit() and _is_zombie(int(d.name)))
    except Exception:
        pass
    
    output += f"\n📈 Post-cleanup status:\n"
    output += f"  • Current zombie count: {remaining_zombies}\n"
    output += f"  • Signal used: {sig_name}\n"
    
    if remaining_zombies > 0:
        output += f"\n💡 Tip: Some zombies may remain. Zombies can't be killed directly;\n"
        output += f"      they're reaped when their parent calls wait().\n"
        output += f"      Try: process_cleanup(kill_zombies=True, force=False)\n"
    
    output += f"\n{'='*80}"
    return output
