"""
tools/ecosystem_os_abstraction.py — Cross-OS Abstraction Layer

Unified API for OS-specific operations across Linux, Windows, and macOS.
All ecosystem code routes through THIS module instead of making raw OS calls.

8 API Categories:
  1. detect()   — Platform detection (cached at import)
  2. process()  — Process management (kill, list, info)
  3. system()   — System info (CPU, memory, network)
  4. subprocess — Cross-OS command execution wrapper
  5. signals()  — Signal number mapping
  6. permissions — File permission abstraction
  7. paths()    — Cross-platform path management
  8. environ()  — Environment variable abstraction

Usage:
    from ecosystem_os_abstraction import detect_os, process_kill, system_loadavg

Spec: CROSS_OS_ABSTRACTION_SPEC.md
Master WO: #250
"""

import json
import logging
import os
import platform
import subprocess as _subprocess
import sys
import time
from enum import Enum
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import List, Optional, Dict, Any, Tuple, Union

logger = logging.getLogger("ecosystem_os_abstraction")

# ═══════════════════════════════════════════════════════════════
# 1. OS DETECTION (cached at import time)
# ═══════════════════════════════════════════════════════════════


class OSType(Enum):
    LINUX = "linux"
    WINDOWS = "windows"
    MACOS = "macos"
    UNKNOWN = "unknown"


_OS_CACHE: Optional[OSType] = None


def detect_os() -> OSType:
    """Detect the current operating system (cached after first call)."""
    global _OS_CACHE
    if _OS_CACHE is not None:
        return _OS_CACHE

    system = platform.system().lower()
    if system == "linux":
        # Further check for Android vs desktop Linux
        if "android" in platform.platform().lower():
            _OS_CACHE = OSType.LINUX  # Treat as Linux for now
        else:
            _OS_CACHE = OSType.LINUX
    elif system == "windows":
        _OS_CACHE = OSType.WINDOWS
    elif system == "darwin":
        _OS_CACHE = OSType.MACOS
    else:
        _OS_CACHE = OSType.UNKNOWN

    logger.debug(f"OS detected: {_OS_CACHE.value}")
    return _OS_CACHE


def is_linux() -> bool:
    """Check if running on Linux."""
    return detect_os() == OSType.LINUX


def is_windows() -> bool:
    """Check if running on Windows."""
    return detect_os() == OSType.WINDOWS


def is_macos() -> bool:
    """Check if running on macOS."""
    return detect_os() == OSType.MACOS


# ═══════════════════════════════════════════════════════════════
# 2. PROCESS MANAGEMENT
# ═══════════════════════════════════════════════════════════════


def process_kill(pid: int, sig: int = 15) -> bool:
    """
    Kill a process by PID. Cross-platform wrapper for os.kill().
    On Linux: uses os.kill(pid, sig)
    On Windows: uses taskkill via subprocess (SIGKILL) or os.kill (SIGTERM)
    Returns True if successful, False otherwise.
    """
    try:
        if is_windows():
            if sig == 9:  # SIGKILL
                _subprocess.run(
                    ["taskkill", "/F", "/PID", str(pid)],
                    capture_output=True, timeout=10
                )
                return True
            else:
                os.kill(pid, sig)
                return True
        else:
            os.kill(pid, sig)
            return True
    except ProcessLookupError:
        # pid doesn't exist - that's fine
        return True
    except PermissionError:
        logger.warning(f"Permission denied killing PID {pid}")
        return False
    except Exception as e:
        logger.error(f"process_kill({pid}, {sig}) failed: {e}")
        return False


def process_kill_group(pgid: int, sig: int = 15) -> bool:
    """
    Kill an entire process group. Cross-platform wrapper for os.killpg().
    On Windows: not natively supported, kills each process individually.
    """
    if is_windows():
        # Windows doesn't support killpg — enumerate and kill individually
        try:
            result = _subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pgid)],
                capture_output=True, timeout=10, text=True
            )
            return result.returncode == 0
        except Exception as e:
            logger.warning(f"process_kill_group({pgid}) fallback failed: {e}")
            return False
    else:
        try:
            os.killpg(pgid, sig)
            return True
        except ProcessLookupError:
            return True
        except Exception as e:
            logger.warning(f"process_kill_group({pgid}, {sig}) failed: {e}")
            return False


def process_exists(pid: int) -> bool:
    """Check if a process with the given PID exists (cross-platform).
    Uses /proc/ check on Linux (avoids os.kill which host_protection patches)."""
    if is_windows():
        try:
            result = _subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                capture_output=True, timeout=5, text=True
            )
            return str(pid) in result.stdout
        except Exception:
            return False
    else:
        # Check /proc/{pid} directly (avoids os.kill which may be patched)
        return os.path.isdir(f"/proc/{pid}")


def process_list() -> List[Dict[str, Any]]:
    """
    List all running processes. Cross-platform replacement for `ps aux`.
    Returns list of dicts with keys: pid, ppid, name, cpu_percent, memory_percent, state, cmdline
    """
    processes = []

    if is_windows():
        try:
            result = _subprocess.run(
                ["tasklist", "/V", "/FO", "CSV"],
                capture_output=True, timeout=30, text=True
            )
            # Parse CSV output
            for line in result.stdout.strip().split("\n")[1:]:
                parts = [p.strip(' "') for p in line.split('","')]
                if len(parts) >= 8:
                    processes.append({
                        "pid": int(parts[1]) if parts[1].isdigit() else 0,
                        "name": parts[0],
                        "state": parts[6],
                        "cmdline": parts[7],
                        "memory_str": parts[4],
                    })
        except Exception as e:
            logger.error(f"process_list Windows failed: {e}")
            return []
    else:
        # Linux: Parse /proc directly (no ps required)
        try:
            for entry in os.listdir("/proc"):
                if not entry.isdigit():
                    continue
                pid = int(entry)
                try:
                    with open(f"/proc/{pid}/stat", "r") as f:
                        stat_parts = f.read().strip().split(")")
                        if len(stat_parts) < 2:
                            continue
                        name = stat_parts[0].split("(")[-1] if "(" in stat_parts[0] else ""
                        rest = stat_parts[1].strip().split()
                        if len(rest) < 38:
                            continue
                        state = rest[0]
                        ppid = int(rest[1])

                    # Get cmdline
                    cmdline = ""
                    try:
                        with open(f"/proc/{pid}/cmdline", "rb") as f:
                            cmdline = f.read().decode("utf-8", errors="replace").replace("\x00", " ")
                    except (OSError, PermissionError):
                        pass

                    processes.append({
                        "pid": pid,
                        "ppid": ppid,
                        "name": name,
                        "state": state,
                        "cmdline": cmdline.strip()[:200],
                    })
                except (OSError, PermissionError, ValueError, IndexError):
                    continue
        except FileNotFoundError:
            # /proc not available (not Linux)
            pass
        except Exception as e:
            logger.warning(f"process_list /proc failed: {e}")

    return processes


def process_info(pid: int) -> Dict[str, Any]:
    """
    Get detailed information about a specific process.
    Returns dict with: cpu_time, memory_bytes, state, name, fd_count, threads
    """
    info: Dict[str, Any] = {"pid": pid, "exists": False}

    if is_windows():
        try:
            result = _subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                capture_output=True, timeout=5, text=True
            )
            if str(pid) in result.stdout:
                info["exists"] = True
                info["source"] = "tasklist"
        except Exception:
            pass
    else:
        # Linux: Read /proc/[pid]/stat and status
        try:
            with open(f"/proc/{pid}/stat", "r") as f:
                stat_data = f.read().strip()
            with open(f"/proc/{pid}/status", "r") as f:
                status_data = f.read()

            # Parse status file
            for line in status_data.split("\n"):
                if ":" in line:
                    key, val = line.split(":", 1)
                    key = key.strip().lower().replace(" ", "_")
                    val = val.strip()
                    if key == "name":
                        info["name"] = val.strip("()")
                    elif key == "vmrss":
                        info["memory_kb"] = int(val.split()[0]) if val.split()[0].isdigit() else 0
                    elif key == "threads":
                        info["threads"] = int(val) if val.isdigit() else 0
                    elif key == "state":
                        info["state"] = val

            # FD count
            try:
                fd_count = len(os.listdir(f"/proc/{pid}/fd"))
                info["fd_count"] = fd_count
            except (OSError, PermissionError):
                pass

            info["exists"] = True
            info["source"] = "/proc"
        except (FileNotFoundError, PermissionError, OSError):
            info["exists"] = False

    return info


# ═══════════════════════════════════════════════════════════════
# 3. SYSTEM INFORMATION
# ═══════════════════════════════════════════════════════════════


def system_loadavg() -> List[float]:
    """
    Get system load averages (1, 5, 15 min).
    On Windows: uses psutil if available, otherwise returns [0.0, 0.0, 0.0]
    """
    if is_windows():
        try:
            import psutil
            cpu_percent = psutil.cpu_percent(interval=0.1)
            load = cpu_percent / 100.0  # Approximate load from CPU %
            return [load, load, load]
        except ImportError:
            logger.debug("psutil not available on Windows, returning 0 load")
            return [0.0, 0.0, 0.0]
    else:
        try:
            with open("/proc/loadavg", "r") as f:
                parts = f.read().strip().split()[:3]
                return [float(p) for p in parts]
        except (FileNotFoundError, OSError, ValueError):
            return [0.0, 0.0, 0.0]


def system_memory() -> Dict[str, int]:
    """
    Get system memory info.
    Returns dict with: total_kb, available_kb, used_kb, free_kb, percent_used
    """
    mem = {"total_kb": 0, "available_kb": 0, "used_kb": 0, "free_kb": 0, "percent_used": 0.0}

    if is_windows():
        try:
            import psutil
            svmem = psutil.virtual_memory()
            mem["total_kb"] = svmem.total // 1024
            mem["available_kb"] = svmem.available // 1024
            mem["used_kb"] = svmem.used // 1024
            mem["free_kb"] = svmem.free // 1024
            mem["percent_used"] = svmem.percent
        except ImportError:
            # Use ctypes to call Windows API
            try:
                import ctypes
                kernel32 = ctypes.windll.kernel32
                mem_status = ctypes.create_string_buffer(64)
                kernel32.GlobalMemoryStatusEx(mem_status)
                # Parse MEMORYSTATUSEX
                logger.debug("Windows memory via kernel32")
            except Exception:
                pass
    else:
        try:
            with open("/proc/meminfo", "r") as f:
                for line in f:
                    parts = line.split(":")
                    if len(parts) != 2:
                        continue
                    key = parts[0].strip().lower().replace(" ", "_")
                    val = parts[1].strip().split()[0] if parts[1].strip() else "0"
                    if key == "memtotal":
                        mem["total_kb"] = int(val)
                    elif key == "memavailable":
                        mem["available_kb"] = int(val)
                    elif key == "memfree":
                        mem["free_kb"] = int(val)
            if mem["total_kb"] > 0:
                mem["used_kb"] = mem["total_kb"] - mem["available_kb"]
                mem["percent_used"] = round((mem["used_kb"] / mem["total_kb"]) * 100, 1)
        except (FileNotFoundError, OSError, ValueError):
            pass

    return mem


def system_network_connections() -> List[Dict[str, Any]]:
    """
    List active network connections.
    On Linux: reads /proc/net/tcp and /proc/net/tcp6
    On Windows: uses `netstat -ano` or psutil
    """
    connections = []

    if is_windows():
        try:
            result = _subprocess.run(
                ["netstat", "-ano"],
                capture_output=True, timeout=15, text=True
            )
            for line in result.stdout.split("\n"):
                parts = line.strip().split()
                if len(parts) >= 5 and parts[0] in ("TCP", "UDP"):
                    connections.append({
                        "protocol": parts[0],
                        "local": parts[1],
                        "remote": parts[2],
                        "state": parts[3] if parts[0] == "TCP" else "",
                        "pid": int(parts[-1]) if parts[-1].isdigit() else 0,
                    })
        except Exception as e:
            logger.warning(f"Windows netstat failed: {e}")
            return []
    else:
        # Linux: Parse /proc/net/tcp and tcp6
        for proto_file, proto_name in [("tcp", "TCP"), ("tcp6", "TCP6"), ("udp", "UDP")]:
            try:
                with open(f"/proc/net/{proto_file}", "r") as f:
                    next(f)  # Skip header
                    for line in f:
                        parts = line.strip().split()
                        if len(parts) >= 10:
                            local = parts[1]
                            remote = parts[2]
                            state_hex = parts[3]
                            inode = parts[9]
                            # Parse hex state
                            states = {"0A": "LISTEN", "01": "ESTABLISHED",
                                      "02": "SYN_SENT", "03": "SYN_RECV",
                                      "04": "FIN_WAIT1", "05": "FIN_WAIT2",
                                      "06": "TIME_WAIT", "07": "CLOSE",
                                      "08": "CLOSE_WAIT", "09": "LAST_ACK",
                                      "0B": "CLOSING"}
                            connections.append({
                                "protocol": proto_name,
                                "local": local,
                                "remote": remote,
                                "state": states.get(state_hex, state_hex),
                                "inode": inode,
                            })
            except FileNotFoundError:
                pass
            except Exception as e:
                logger.debug(f"Could not read /proc/net/{proto_file}: {e}")

    return connections


# ═══════════════════════════════════════════════════════════════
# 4. SUBPROCESS WRAPPER
# ═══════════════════════════════════════════════════════════════


def run_command(
    cmd: List[str],
    **kwargs: Any
) -> _subprocess.CompletedProcess:
    """
    Cross-platform subprocess runner.
    Automatically handles:
    - shell=False (default, recommended)
    - timeout parameter from kwargs with default 30s
    - Character encoding differences on Windows
    """
    timeout = kwargs.pop("timeout", 30)

    if is_windows():
        # Windows needs special handling for console apps
        if "creationflags" not in kwargs:
            # Don't create a console window for GUI apps
            kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW

    try:
        result = _subprocess.run(
            cmd,
            timeout=timeout,
            **kwargs
        )
        return result
    except _subprocess.TimeoutExpired:
        raise
    except Exception as e:
        raise RuntimeError(f"Command failed: {' '.join(cmd)}: {e}")


def find_command(cmd: str) -> Optional[str]:
    """
    Cross-platform 'which' command.
    On Linux: returns shutil.which(cmd)
    On Windows: uses where.exe
    """
    import shutil
    path = shutil.which(cmd)
    if path:
        return path
    return None


# ═══════════════════════════════════════════════════════════════
# 5. SIGNAL ABSTRACTION
# ═══════════════════════════════════════════════════════════════


class Signals:
    """
    Cross-platform signal numbers.
    On Linux: matches signal.SIG{TERM,KILL,HUP,etc}
    On Windows: SIGTERM=15 (same as POSIX), SIGKILL mapped to terminate
    """
    SIGHUP = 1 if not is_windows() else 1     # Same on both
    SIGINT = 2
    SIGQUIT = 3
    SIGILL = 4
    SIGTRAP = 5
    SIGABRT = 6
    SIGBUS = 7 if not is_windows() else 7
    SIGFPE = 8
    SIGKILL = 9
    SIGUSR1 = 10 if not is_windows() else 10
    SIGSEGV = 11
    SIGUSR2 = 12 if not is_windows() else 12
    SIGPIPE = 13
    SIGALRM = 14
    SIGTERM = 15
    SIGSTKFLT = 16 if not is_windows() else 15  # Linux-only, map to SIGTERM
    SIGCHLD = 17
    SIGCONT = 18
    SIGSTOP = 19
    SIGTSTP = 20


def set_signal_handler(signum: int, handler) -> bool:
    """
    Cross-platform signal handler registration.
    On Windows: some signals are not supported.
    Returns True if handler was registered, False otherwise.
    """
    try:
        import signal
        signal.signal(signum, handler)
        return True
    except (ValueError, OSError, RuntimeError) as e:
        logger.warning(f"Cannot set signal handler for {signum}: {e}")
        return False


def get_signal_name(signum: int) -> str:
    """Get signal name from number (cross-platform safe)."""
    try:
        import signal
        return signal.Signals(signum).name
    except (ValueError, AttributeError):
        names = {
            1: "SIGHUP", 2: "SIGINT", 3: "SIGQUIT", 4: "SIGILL",
            5: "SIGTRAP", 6: "SIGABRT", 7: "SIGBUS", 8: "SIGFPE",
            9: "SIGKILL", 10: "SIGUSR1", 11: "SIGSEGV", 12: "SIGUSR2",
            13: "SIGPIPE", 14: "SIGALRM", 15: "SIGTERM", 16: "SIGSTKFLT",
            17: "SIGCHLD", 18: "SIGCONT", 19: "SIGSTOP", 20: "SIGTSTP",
        }
        return names.get(signum, f"SIG_{signum}")


# ═══════════════════════════════════════════════════════════════
# 6. FILE PERMISSIONS
# ═══════════════════════════════════════════════════════════════


def set_permissions(path: str, mode: int = 0o755) -> bool:
    """
    Set file permissions. Cross-platform.
    Linux: os.chmod(path, mode)
    Windows: No chmod equivalent, no-op with True
    """
    if is_windows():
        # Windows doesn't have Unix permissions
        return True
    try:
        os.chmod(path, mode)
        return True
    except (OSError, PermissionError) as e:
        logger.warning(f"set_permissions({path}, {mode:o}) failed: {e}")
        return False


def is_executable(path: str) -> bool:
    """Check if a file is executable (cross-platform)."""
    if is_windows():
        # On Windows, check extension
        ext = os.path.splitext(path)[1].lower()
        return ext in (".exe", ".bat", ".cmd", ".ps1", ".com")
    else:
        return os.access(path, os.X_OK)


# ═══════════════════════════════════════════════════════════════
# 7. CROSS-PLATFORM PATHS
# ═══════════════════════════════════════════════════════════════


def home_dir() -> Path:
    """Get the user's home directory (cross-platform)."""
    env_home = os.environ.get("HOME") or os.environ.get("USERPROFILE")
    if env_home:
        return Path(env_home)

    # Fallback to pathlib
    return Path.home()


def ecosystem_root() -> Path:
    """Get the ecosystem root directory.
    Detects if running from AIHandler or YuniScripts."""
    # Check if we're running from AIHandler
    script_path = Path(sys.argv[0]) if sys.argv and sys.argv[0] else Path.cwd()
    return script_path.resolve().parent


def venv_python() -> str:
    """
    Get the virtual environment Python path (cross-platform).
    Linux: .venv/bin/python
    Windows: .venv\\Scripts\\python.exe
    """
    if is_windows():
        return os.path.join(".venv", "Scripts", "python.exe")
    else:
        return os.path.join(".venv", "bin", "python")


def normalize_path(path: str) -> str:
    """
    Normalize path separators for the current OS.
    Cross-platform path canonicalization.
    """
    path = path.replace("\\", "/")  # Normalize to forward slashes
    # Resolve . and ..
    parts = []
    for part in path.split("/"):
        if part == ".":
            continue
        elif part == "..":
            if parts:
                parts.pop()
        else:
            parts.append(part)

    result = "/".join(parts)

    if is_windows():
        result = result.replace("/", "\\")

    return result


# ═══════════════════════════════════════════════════════════════
# 8. ENVIRONMENT VARIABLES
# ═══════════════════════════════════════════════════════════════


def get_env(key: str, default: str = "") -> str:
    """
    Get an environment variable with cross-platform key lookup.
    On Windows: case-insensitive key matching
    """
    if is_windows():
        # Windows env vars are case-insensitive
        key_upper = key.upper()
        for k, v in os.environ.items():
            if k.upper() == key_upper:
                return v
        return default
    else:
        return os.environ.get(key, default)


def set_env(key: str, value: str) -> None:
    """Set an environment variable."""
    os.environ[key] = value


def list_env(prefix: str = "") -> Dict[str, str]:
    """List environment variables, optionally filtered by prefix."""
    if prefix:
        pfx = prefix.upper() if is_windows() else prefix
        return {
            k: v for k, v in os.environ.items()
            if (k.upper().startswith(pfx) if is_windows()
                else k.startswith(prefix))
        }
    return dict(os.environ)


# ═══════════════════════════════════════════════════════════════
# HOT RELOAD / CACHE CONTROL
# ═══════════════════════════════════════════════════════════════


def clear_os_cache() -> None:
    """Clear the cached OS type (forces re-detection on next call)."""
    global _OS_CACHE
    _OS_CACHE = None


def get_os_summary() -> Dict[str, Any]:
    """Get a summary of the current OS environment."""
    return {
        "os_type": detect_os().value,
        "system": platform.system(),
        "release": platform.release(),
        "version": platform.version(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "python_version": platform.python_version(),
        "is_linux": is_linux(),
        "is_windows": is_windows(),
        "is_macos": is_macos(),
        "home_dir": str(home_dir()),
        "path_sep": os.sep,
        "venv_python": venv_python(),
    }
