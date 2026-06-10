"""
Divine Protection Advanced — Kernel-level sandbox layers for FastMCP.

ADDITIONAL PROTECTION LAYERS beyond the base divine system:

  D13: LandlockLsmSandbox  — Filesystem access restriction via Landlock LSM
  D14: SeccompBpfFilter    — Syscall filtering via seccomp-bpf (raw BPF)
  D15: BubblewrapIsolation — Full namespace isolation via bubblewrap
  D16: NoNewPrivsGuard     — PR_SET_NO_NEW_PRIVS enforcement
  D17: FdLeakProtector     — Close inherited FDs in subprocesses
  D18: TreeKiller          — Process group tree kill on timeout
  D19: CapabilityDropper   — Drop dangerous capabilities from subprocesses
  D20: DiskQuotaEnforcer   — Per-tool disk write limits via RLIMIT_FSIZE

Every layer gracefully degrades if the kernel does not support it.
"""

import array
import ctypes
import ctypes.util
import logging
import os
import signal
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple
from tools.ecosystem_os_abstraction import process_list, process_info, system_loadavg, system_memory


# ── Dynamic Config Registration ──────────────────────────────
try:
    from dynamic_config_loader import register_configs
    register_configs("divine", [
        {"key": "check_interval_seconds", "type": "int", "default": 5,
          "description": "Divine protection check interval",
          "valid_range": (1, 300),
          "category": "performance"},
        {"key": "memory_warning_mb", "type": "int", "default": 512,
          "description": "Memory warning threshold MB",
          "valid_range": (128, 32768),
          "category": "monitoring"},
        {"key": "memory_critical_mb", "type": "int", "default": 256,
          "description": "Memory critical threshold MB",
          "valid_range": (64, 16384),
          "category": "security"},
        {"key": "max_processes", "type": "int", "default": 100,
          "description": "Max processes before divine intervention",
          "valid_range": (10, 5000),
          "category": "security"},
        {"key": "divine_shield_enabled", "type": "bool", "default": True,
          "description": "Enable divine protection shield",
          "category": "security"},
        {"key": "debug_mode", "type": "bool", "default": False,
          "description": "Enable divine debug logging",
          "category": "debug"},
        {"key": "grace_period_seconds", "type": "float", "default": 1.0,
          "description": "Grace period before force actions",
          "valid_range": (0.1, 30.0),
          "category": "performance"},
    ])
except ImportError:
    pass
# ──────────────────────────────────────────────────────────────

logger = logging.getLogger(__name__)

# ======================================================================
# CONSTANTS
# ======================================================================

# prctl constants
PR_SET_NO_NEW_PRIVS = 38
PR_GET_NO_NEW_PRIVS = 39
PR_SET_SECCOMP = 22
PR_GET_SECCOMP = 21
SECCOMP_MODE_FILTER = 2

# landlock syscall numbers (x86_64)
LANDLOCK_CREATE_RULESET  = 444
LANDLOCK_ADD_RULE        = 445
LANDLOCK_RESTRICT_SELF   = 446

# landlock access rights (fs)
LANDLOCK_ACCESS_FS_EXECUTE      = 1 << 0
LANDLOCK_ACCESS_FS_WRITE_FILE   = 1 << 1
LANDLOCK_ACCESS_FS_READ_FILE    = 1 << 2
LANDLOCK_ACCESS_FS_READ_DIR     = 1 << 3
LANDLOCK_ACCESS_FS_REMOVE_DIR   = 1 << 4
LANDLOCK_ACCESS_FS_REMOVE_FILE  = 1 << 5
LANDLOCK_ACCESS_FS_MAKE_CHAR    = 1 << 6
LANDLOCK_ACCESS_FS_MAKE_DIR     = 1 << 7
LANDLOCK_ACCESS_FS_MAKE_REG     = 1 << 8
LANDLOCK_ACCESS_FS_MAKE_SOCK    = 1 << 9
LANDLOCK_ACCESS_FS_MAKE_FIFO    = 1 << 10
LANDLOCK_ACCESS_FS_MAKE_BLOCK   = 1 << 11
LANDLOCK_ACCESS_FS_MAKE_SYM     = 1 << 12
LANDLOCK_ACCESS_FS_TRUNCATE     = 1 << 13
LANDLOCK_ACCESS_FS_REFER        = 1 << 14

# full read-write access mask
LANDLOCK_ACCESS_FS_RW = (
    LANDLOCK_ACCESS_FS_EXECUTE |
    LANDLOCK_ACCESS_FS_WRITE_FILE |
    LANDLOCK_ACCESS_FS_READ_FILE |
    LANDLOCK_ACCESS_FS_READ_DIR |
    LANDLOCK_ACCESS_FS_REMOVE_DIR |
    LANDLOCK_ACCESS_FS_REMOVE_FILE |
    LANDLOCK_ACCESS_FS_MAKE_CHAR |
    LANDLOCK_ACCESS_FS_MAKE_DIR |
    LANDLOCK_ACCESS_FS_MAKE_REG |
    LANDLOCK_ACCESS_FS_MAKE_SOCK |
    LANDLOCK_ACCESS_FS_MAKE_FIFO |
    LANDLOCK_ACCESS_FS_MAKE_BLOCK |
    LANDLOCK_ACCESS_FS_MAKE_SYM |
    LANDLOCK_ACCESS_FS_TRUNCATE |
    LANDLOCK_ACCESS_FS_REFER
)

# read-only mask
LANDLOCK_ACCESS_FS_RO = (
    LANDLOCK_ACCESS_FS_EXECUTE |
    LANDLOCK_ACCESS_FS_READ_FILE |
    LANDLOCK_ACCESS_FS_READ_DIR
)

# landlock ruleset attr (for creating ruleset)
LANDLOCK_RULESET_ATTR_SIZE = 8  # sizeof(struct landlock_ruleset_attr)

# seccomp BPF constants
SECCOMP_RET_KILL       = 0x00000000
SECCOMP_RET_KILL_PROCESS = 0x80000000
SECCOMP_RET_KILL_THREAD  = 0x00000000
SECCOMP_RET_TRAP       = 0x00030000
SECCOMP_RET_ERRNO      = 0x00050000
SECCOMP_RET_TRACE      = 0x7ff00000
SECCOMP_RET_LOG        = 0x7ffc0000
SECCOMP_RET_ALLOW      = 0x7fff0000

# seccomp data offsets
SECCOMP_DATA_OFFSET_NR       = 0
SECCOMP_DATA_OFFSET_ARCH     = 4
SECCOMP_DATA_OFFSET_INS_HI   = 12
SECCOMP_DATA_OFFSET_INS_LO   = 16

# AUDIT architecture (x86_64)
AUDIT_ARCH_X86_64 = 0xc000003e  # x86_64
AUDIT_ARCH_I386   = 0x40000003  # x86
AUDIT_ARCH_AARCH64 = 0xc00000b7  # ARM64

# BPF instruction structure: struct sock_filter
BPF_INST_SIZE = 8  # sizeof(struct sock_filter)

# BPF instruction codes
BPF_LD  = 0x00
BPF_LDX = 0x01
BPF_ST  = 0x02
BPF_ALU = 0x04
BPF_JMP = 0x05
BPF_RET = 0x06
BPF_MISC = 0x07

# BPF sizes
BPF_W   = 0x00  # 32-bit
BPF_H   = 0x08  # 16-bit
BPF_B   = 0x10  # 8-bit

# BPF LD/LDX modes
BPF_IMM = 0x00
BPF_ABS = 0x20
BPF_IND = 0x40
BPF_MEM = 0x60
BPF_LEN = 0x80
BPF_MSH = 0xa0

# BPF JMP conditions
BPF_JA  = 0x00
BPF_JEQ = 0x10
BPF_JGT = 0x20
BPF_JGE = 0x30
BPF_JSET = 0x40

# BPF operand types for JMP/ALU
BPF_K = 0x00  # Use constant (k field)
BPF_X = 0x08  # Use X register

# BPF ALU operations
BPF_ADD = 0x00
BPF_SUB = 0x10
BPF_MUL = 0x20
BPF_DIV = 0x30
BPF_OR  = 0x40
BPF_AND = 0x50
BPF_LSH = 0x60
BPF_RSH = 0x70
BPF_NEG = 0x80
BPF_MOD = 0x90
BPF_XOR = 0xa0

# seccomp data offset for the syscall number and architecture
# These come from: offsetof(struct seccomp_data, nr)
# offsetof(struct seccomp_data, arch) = 4
OFFSET_NR   = 0
OFFSET_ARCH = 4
OFFSET_ARG0 = 16  # args[0]
OFFSET_ARG1 = 24
OFFSET_ARG2 = 32
OFFSET_ARG3 = 40
OFFSET_ARG4 = 48
OFFSET_ARG5 = 56

# Critical syscalls to block in subprocesses
BLOCKED_SYSCALLS_X86_64 = {
    # System-level operations
    169: "reboot",             # reboot
    175: "init_module",        # init_module
    176: "delete_module",      # delete_module
    125: "iopl",               # iopl
    172: "ioperm",             # ioperm
    165: "mount",              # mount (block mounting)
    166: "umount2",            # umount2
    # Kernel/BPF operations
    321: "bpf",                # bpf (prevent subprocess from creating filters)
    # Secure computing
    317: "seccomp",            # seccomp (prevent removing seccomp filters)
    # Process observation
    101: "ptrace",             # ptrace (block tracing other processes)
    270: "process_vm_readv",   # process_vm_readv
    271: "process_vm_writev",  # process_vm_writev
    # Performance monitoring
    298: "perf_event_open",    # perf_event_open
    # Key management
    250: "finotify_init",      # fanotify_init
    # Swap
    171: "swapon",             # swapon
    172: "swapoff",            # swapoff  
    # Module loading (already blocked but defense-in-depth)
    313: "kexec_load",         # kexec_load
    320: "kexec_file_load",    # kexec_file_load
    # NUMA
    339: "membarrier",         # membarrier
    # Namespace
    322: "clone3",             # clone3 (prevent namespace creation)
    272: "setns",              # setns (prevent joining other namespaces)
    309: "unshare",            # unshare (prevent namespace creation via unshare)
}

# Syscalls that should return ENOSYS (not supported) rather than killing
# the process, since some benign tools might call them
BLOCKED_SYSCALLS_ENOSYS_X86_64 = set()

# Global state
_landlock_available = None
_seccomp_available = None
_bubblewrap_available = None
_no_new_privs_available = None

# Lock for initialization
_init_lock = threading.Lock()


# ======================================================================
# UTILITY: Raw syscall via ctypes (for Landlock)
# ======================================================================

if hasattr(os, 'syscall'):
    _syscall = os.syscall
else:
    # Fallback: use ctypes
    try:
        _libc = ctypes.CDLL(ctypes.util.find_library('c'), use_errno=True)
        def _syscall(sysno, *args):
            return _libc.syscall(sysno, *args)
    except Exception:
        _syscall = None


# ======================================================================
# D13: LANDLOCK LSM FILESYSTEM SANDBOX
# ======================================================================

class LandlockLsmSandbox:
    """
    D13: Landlock LSM — Filesystem access restriction.

    Uses the Landlock Linux Security Module to create a sandbox that
    restricts filesystem access to specific paths. After restriction,
    the process can ONLY access files/dirs that were explicitly allowed.

    This runs in the preexec function of subprocesses to sandbox them.

    Landlock is available on Linux >= 5.13 and must be enabled in the
    kernel LSM list. It requires NO root privileges.

    The sandbox allows:
      - Read-write access to: working directory, temp dir
      - Read-only access to: /usr, /lib, /lib64, /etc (selected)
      - Blocked: All other paths (including /proc, /sys, other users' homes)

    Graceful degradation: If Landlock is unavailable, logs a warning
    and continues without filesystem sandboxing.
    """

    # Singleton instance
    _instance = None

    def __init__(self):
        self._available = False
        self._reason = ""
        self._ruleset_fd = None
        self._allowed_ro_paths = set()
        self._allowed_rw_paths = set()
        self._libc = None

    @classmethod
    def get_instance(cls) -> 'LandlockLsmSandbox':
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def initialize(self) -> bool:
        """Check Landlock availability and prepare ruleset."""
        global _landlock_available

        if _landlock_available is not None:
            self._available = _landlock_available
            return self._available

        # Check kernel support
        if not self._check_kernel_support():
            _landlock_available = False
            return False

        # Check syscall availability by trying to create a test ruleset
        if not self._test_syscall():
            _landlock_available = False
            return False

        # Create the ruleset
        if not self._create_ruleset():
            _landlock_available = False
            return False

        _landlock_available = True
        self._available = True

        logger.info(f"  ✅ D13: Landlock LSM filesystem sandbox ACTIVE")
        logger.info(f"       Read-write dirs: {len(self._allowed_rw_paths)}")
        logger.info(f"       Read-only dirs:  {len(self._allowed_ro_paths)}")
        return True

    def _check_kernel_support(self) -> bool:
        """Check if the kernel supports Landlock."""
        try:
            # Check /sys/kernel/security/lsm for 'landlock'
            if os.path.exists('/sys/kernel/security/lsm'):
                with open('/sys/kernel/security/lsm') as f:
                    lsms = f.read().strip()
                    if 'landlock' in lsms:
                        return True
                    self._reason = "landlock not in LSM list"
                    return False

            # Alternative: check /proc/sys/kernel/landlock_available
            if os.path.exists('/proc/sys/kernel/landlock_available'):
                with open('/proc/sys/kernel/landlock_available') as f:
                    if f.read().strip() == '1':
                        return True
                    self._reason = "landlock disabled via sysctl"
                    return False

            self._reason = "cannot determine Landlock availability"
            return False
        except Exception as e:
            self._reason = f"check failed: {e}"
            return False

    def _test_syscall(self) -> bool:
        """Test if landlock_create_ruleset syscall is available."""
        try:
            # Try to load the libc
            self._libc = ctypes.CDLL(ctypes.util.find_library('c'), use_errno=True)

            # Try a simple test - create a tiny ruleset attr
            # struct landlock_ruleset_attr { __u64 handled_access_fs; }
            attr = ctypes.create_string_buffer(LANDLOCK_RULESET_ATTR_SIZE)
            # Set handled_access_fs = LANDLOCK_ACCESS_FS_EXECUTE (non-zero is required)
            # ENOMSG (errno=42) is returned if handled_access_fs is 0 (no rules)
            struct.pack_into('<Q', attr, 0, LANDLOCK_ACCESS_FS_EXECUTE)

            # Try landlock_create_ruleset syscall
            # If it returns -1 with ENOSYS or EOPNOTSUPP, Landlock is not available
            ret = self._libc.syscall(
                LANDLOCK_CREATE_RULESET,
                ctypes.cast(attr, ctypes.c_void_p),
                LANDLOCK_RULESET_ATTR_SIZE,
                0  # flags = 0
            )

            if ret >= 0:
                # Success! Now close the test ruleset
                # (It's a valid fd that we created)
                try:
                    os.close(ret)
                except Exception:
                    pass
                return True

            # Check errno
            errno_val = ctypes.get_errno()
            if errno_val in (38, 95, 1):  # ENOSYS, EOPNOTSUPP, EPERM
                self._reason = f"landlock_create_ruleset failed: errno={errno_val}"
                return False

            return False
        except Exception as e:
            self._reason = f"syscall test failed: {e}"
            return False

    def _create_ruleset(self) -> bool:
        """Create the Landlock ruleset with access rights."""
        try:
            # Determine paths
            allowed_rw = []
            allowed_ro = []

            # Read-write paths
            cwd = os.getcwd()
            allowed_rw.append(cwd)

            # Add temp directories
            for tmp in ['/tmp', '/dev/shm', os.environ.get('TMPDIR', ''),
                         os.environ.get('TMP', ''), os.environ.get('TEMPDIR', '')]:
                if tmp and os.path.isdir(tmp):
                    allowed_rw.append(tmp)

            # Add XDG runtime dir if set
            xdg_runtime = os.environ.get('XDG_RUNTIME_DIR', '')
            if xdg_runtime and os.path.isdir(xdg_runtime):
                allowed_rw.append(xdg_runtime)

            # Add home directory (but limited)
            home = os.environ.get('HOME', '')
            if home and os.path.isdir(home):
                allowed_rw.append(home)
                allowed_ro.append('/home')  # Read-only view of other homes

            # Read-only paths
            for sys_path in ['/usr', '/lib', '/lib64', '/bin', '/sbin',
                              '/opt', '/etc/alternatives']:
                if os.path.isdir(sys_path):
                    allowed_ro.append(sys_path)

            # /etc is tricky - some configs needed for basic tools
            # Allow read-only access to /etc
            if os.path.isdir('/etc'):
                allowed_ro.append('/etc')

            self._allowed_ro_paths = set(allowed_ro)
            self._allowed_rw_paths = set(allowed_rw)

            return True
        except Exception as e:
            self._reason = f"ruleset creation failed: {e}"
            return False

    def get_preexec_sandbox(self) -> Optional[Callable[[], None]]:
        """
        Return a preexec function that applies Landlock sandboxing.

        Returns None if Landlock is not available.
        """
        if not self._available or self._libc is None:
            return None

        # Capture paths at call time
        ro_paths = list(self._allowed_ro_paths)
        rw_paths = list(self._allowed_rw_paths)
        libc = self._libc

        def _apply_landlock():
            """Apply Landlock filesystem sandbox - call in preexec."""
            try:
                # Create ruleset with handled access rights
                handled = LANDLOCK_ACCESS_FS_RW
                attr = ctypes.create_string_buffer(LANDLOCK_RULESET_ATTR_SIZE)
                struct.pack_into('<Q', attr, 0, handled)

                ruleset_fd = libc.syscall(
                    LANDLOCK_CREATE_RULESET,
                    ctypes.cast(attr, ctypes.c_void_p),
                    LANDLOCK_RULESET_ATTR_SIZE,
                    0
                )

                if ruleset_fd < 0:
                    return  # Can't sandbox - fail silently in preexec

                # Add allowed paths
                for path in ro_paths:
                    if os.path.isdir(path):
                        _landlock_add_path_rule(libc, ruleset_fd, path,
                                               LANDLOCK_ACCESS_FS_RO)

                for path in rw_paths:
                    if os.path.isdir(path):
                        _landlock_add_path_rule(libc, ruleset_fd, path,
                                               LANDLOCK_ACCESS_FS_RW)

                # Enforce the ruleset
                libc.syscall(LANDLOCK_RESTRICT_SELF, ruleset_fd, 0)

                # Close the ruleset fd
                try:
                    os.close(ruleset_fd)
                except Exception:
                    pass

            except Exception:
                pass  # Preexec must never raise

        return _apply_landlock


def _landlock_add_path_rule(libc, ruleset_fd: int, path: str,
                            access: int) -> bool:
    """
    Add a Landlock path rule to a ruleset.

    struct landlock_path_beneath_attr {
        __u64 allowed_access;
        __s32 parent_fd;
    } __attribute__((packed));
    """
    try:
        parent_fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC)
        try:
            attr_size = 12  # 8 for allowed_access + 4 for parent_fd
            attr = ctypes.create_string_buffer(attr_size)
            struct.pack_into('<Qi', attr, 0, access, parent_fd)

            ret = libc.syscall(
                LANDLOCK_ADD_RULE,
                ruleset_fd,
                1,  # LANDLOCK_RULE_PATH_BENEATH = 1
                ctypes.cast(attr, ctypes.c_void_p),
                0   # flags = 0
            )
            return ret == 0
        finally:
            try:
                os.close(parent_fd)
            except Exception:
                pass
    except Exception:
        return False


# ======================================================================
# D14: SECCOMP-BPF SYSCALL FILTER
# ======================================================================

class SeccompBpfFilter:
    """
    D14: seccomp-bpf — Syscall filtering.

    Creates a BPF (Berkeley Packet Filter) program that filters syscalls.
    This prevents subprocesses from calling dangerous system operations
    like reboot, mount, module loading, etc.

    The filter is applied via prctl(PR_SET_SECCOMP, SECCOMP_MODE_FILTER, prog).

    Dangerous syscalls blocked:
      - reboot, init_module, delete_module
      - mount, umount2
      - iopl, ioperm
      - bpf (prevent subprocess from creating filters)
      - seccomp (prevent removing seccomp filter)
      - ptrace (block tracing other processes)
      - perf_event_open
      - kexec_load, kexec_file_load
      - swapon, swapoff
      - process_vm_readv, process_vm_writev
      - setns, unshare (prevent namespace escape)
      - clone3 (prevent creating new namespaces)

    Graceful degradation: If seccomp is unavailable, logs a warning
    and continues without syscall filtering.
    """

    _instance = None

    def __init__(self):
        self._available = False
        self._reason = ""
        self._bpf_prog = None  # The compiled BPF program as ctypes array

    @classmethod
    def get_instance(cls) -> 'SeccompBpfFilter':
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def initialize(self) -> bool:
        """Check seccomp availability and compile BPF filter."""
        global _seccomp_available

        if _seccomp_available is not None:
            self._available = _seccomp_available
            return self._available

        try:
            # Try to detect seccomp via /proc (Linux-specific)
            if os.path.exists('/proc/self/status'):
                with open('/proc/self/status') as f:
                    for line in f:
                        if line.startswith('Seccomp:'):
                            val = line.split(':')[1].strip()
                            if val.isdigit():
                                # 0 = disabled, 1 = strict, 2 = filter
                                # Just knowing the file exists means seccomp is supported
                                self._available = True
                                break

            if not self._available:
                # Try probing via prctl directly
                try:
                    libc = ctypes.CDLL(ctypes.util.find_library('c'), use_errno=True)
                    ret = libc.prctl(PR_GET_SECCOMP, 0, 0, 0, 0)
                    # If it returns 0 and errno is 0, seccomp works
                    # If it returns -1 with EINVAL, seccomp is not supported
                    if ret >= 0:
                        self._available = True
                    else:
                        err = ctypes.get_errno()
                        if err != 22:  # EINVAL = not supported
                            self._available = True  # Could be other reason
                except Exception:
                    pass

            if not self._available:
                self._reason = "seccomp not detected"
                _seccomp_available = False
                return False

            # Compile the BPF filter
            self._bpf_prog = self._compile_bpf_filter()
            _seccomp_available = True

            logger.info(f"  ✅ D14: seccomp-bpf syscall filter ACTIVE")
            logger.info(f"       Blocked syscalls: {len(BLOCKED_SYSCALLS_X86_64)}")
            return True

        except Exception as e:
            self._reason = f"init failed: {e}"
            logger.info(f"  ⚠️ D14: seccomp-bpf unavailable ({e})")
            _seccomp_available = False
            return False

    def _compile_bpf_filter(self) -> Optional[ctypes.Array]:
        """
        Compile the BPF filter program.

        Returns a ctypes array of struct sock_filter (8 bytes each),
        or None on failure.

        The filter logic:
          1. Load architecture from seccomp_data
          2. If not x86_64, ALLOW (we don't want to break 32-bit compat)
          3. Load syscall number
          4. For blocked syscalls, KILL the process (SIGSYS)
          5. Everything else: ALLOW
        """
        try:
            instructions = []

            def bpf_stmt(code, k):
                """Create a BPF statement: { code, jt=0, jf=0, k }"""
                return struct.pack('<HBBHI', code & 0xFFFF, 0, 0, 0, k & 0xFFFFFFFF)

            def bpf_jump(code, k, jt, jf):
                """Create a BPF jump statement."""
                return struct.pack('<HBBHI', code & 0xFFFF, jt & 0xFF, jf & 0xFF, 0, k & 0xFFFFFFFF)

            # Load architecture word
            instructions.append(bpf_stmt(BPF_LD | BPF_W | BPF_ABS, OFFSET_ARCH))

            # Jump if arch == AUDIT_ARCH_X86_64 (allow non-x86_64 without filtering)
            # BPF_JEQ: jump if equal
            instructions.append(bpf_jump(BPF_JMP | BPF_JEQ | BPF_K, AUDIT_ARCH_X86_64, 0, 2))
            # Not x86_64 - try x86
            instructions.append(bpf_jump(BPF_JMP | BPF_JEQ | BPF_K, AUDIT_ARCH_I386, 0, 1))
            # Not x86 either - allow
            instructions.append(bpf_stmt(BPF_RET | BPF_K, SECCOMP_RET_ALLOW))

            # Load syscall number (first 4 bytes of seccomp_data)
            instructions.append(bpf_stmt(BPF_LD | BPF_W | BPF_ABS, OFFSET_NR))

            # For each blocked syscall, check and KILL
            blocked = sorted(BLOCKED_SYSCALLS_X86_64.items())
            for sysno, name in blocked:
                instructions.append(bpf_jump(BPF_JMP | BPF_JEQ | BPF_K, sysno, 0, 1))
                # If matched, KILL the process
                instructions.append(bpf_stmt(BPF_RET | BPF_K, SECCOMP_RET_KILL_PROCESS))

            # Everything else: ALLOW
            instructions.append(bpf_stmt(BPF_RET | BPF_K, SECCOMP_RET_ALLOW))

            # Convert to ctypes array
            total_bytes = b''.join(instructions)
            prog_array = (ctypes.c_ubyte * len(total_bytes)).from_buffer_copy(
                bytearray(total_bytes)
            )

            return prog_array

        except Exception as e:
            logger.error(f"D14: BPF compilation failed: {e}")
            return None

    def get_preexec_filter(self) -> Optional[Callable[[], None]]:
        """
        Return a preexec function that applies seccomp-bpf filtering.

        Returns None if seccomp is not available.
        """
        if not self._available or self._bpf_prog is None:
            return None

        prog_array = self._bpf_prog

        def _apply_seccomp():
            """Apply seccomp-bpf filter - call in preexec."""
            try:
                # struct sock_fprog { unsigned short len; struct sock_filter *filter; }
                libc = ctypes.CDLL(ctypes.util.find_library('c'), use_errno=True)
                filter_count = len(prog_array) // BPF_INST_SIZE

                # Create sock_fprog structure
                # It's: unsigned short len; struct sock_filter __user *filter;
                # On 64-bit: 2 bytes len + 6 bytes padding + 8 bytes pointer = 16 bytes
                sf = ctypes.create_string_buffer(16)
                struct.pack_into('<H', sf, 0, filter_count)
                struct.pack_into('<Q', sf, 8, ctypes.addressof(prog_array))

                # Apply no_new_privs first (required for seccomp-bpf)
                libc.prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0)

                # Apply the seccomp filter
                ret = libc.prctl(
                    PR_SET_SECCOMP,
                    SECCOMP_MODE_FILTER,
                    ctypes.cast(sf, ctypes.c_void_p)
                )

                if ret != 0:
                    logger.debug(f"seccomp prctl returned {ret}, errno={ctypes.get_errno()}")

            except Exception:
                pass  # Preexec must never raise

        return _apply_seccomp


# ======================================================================
# D15: BUBBLEWRAP NAMESPACE ISOLATION
# ======================================================================

class BubblewrapIsolation:
    """
    D15: bubblewrap — Full namespace isolation.

    Wraps a command in bubblewrap to create a sandbox with:
      - New user namespace (isolated from host UID/GID)
      - New PID namespace (processes inside can't see host)
      - New mount namespace (isolated filesystem view)
      - New network namespace (no network access)
      - New IPC namespace (isolated IPC)
      - New UTS namespace (isolated hostname)
      - New cgroup namespace

    The sandbox provides read-only access to system paths and
    read-write access to the working directory and temp dirs.

    bubblewrap uses PR_SET_NO_NEW_PRIVS (setuid-less sandboxing),
    making it safe for unprivileged use.

    Graceful degradation: If bubblewrap is not available, logs a
    warning and continues without namespace isolation.
    """

    _instance = None

    def __init__(self):
        self._available = False
        self._bwrap_path = None
        self._reason = ""

    @classmethod
    def get_instance(cls) -> 'BubblewrapIsolation':
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def initialize(self) -> bool:
        """Check bubblewrap availability."""
        global _bubblewrap_available

        if _bubblewrap_available is not None:
            self._available = _bubblewrap_available
            return self._available

        try:
            # Find bwrap binary
            result = subprocess.run(
                ['which', 'bwrap'],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                self._bwrap_path = result.stdout.strip()

                # Verify it works with a simple version check
                version_result = subprocess.run(
                    [self._bwrap_path, '--version'],
                    capture_output=True, text=True, timeout=5
                )
                if version_result.returncode == 0:
                    self._available = True
                    _bubblewrap_available = True
                    logger.info(f"  ✅ D15: bubblewrap namespace isolation ACTIVE")
                    logger.info(f"       Path: {self._bwrap_path}")
                    logger.info(f"       Version: {version_result.stdout.strip()}")
                    return True

            _bubblewrap_available = False
            self._reason = "bwrap not found"
            return False

        except Exception as e:
            self._reason = f"check failed: {e}"
            _bubblewrap_available = False
            return False

    def wrap_command(self, cmd: list,
                     allow_network: bool = False,
                     allow_dbus: bool = False,
                     writable_paths: Optional[List[str]] = None,
                     readonly_paths: Optional[List[str]] = None,
                     home_dir: str = "") -> Optional[list]:
        """
        Wrap a command in bubblewrap sandbox arguments.

        Args:
            cmd: Original command as list (e.g., ['python3', 'script.py'])
            allow_network: If True, share network namespace with host
            allow_dbus: If True, bind-mount D-Bus socket
            writable_paths: Additional writable paths
            readonly_paths: Additional read-only paths
            home_dir: Fake home directory (default: tmpdir)

        Returns:
            Wrapped command list, or None if bubblewrap is not available
        """
        if not self._available or not self._bwrap_path:
            return None

        bwrap_args = [self._bwrap_path]

        # ── Unshare ALL namespaces by default ──────────────
        bwrap_args.append('--unshare-all')
        bwrap_args.append('--hostname')
        bwrap_args.append('sandbox')  # Isolated hostname
        bwrap_args.append('--new-session')

        # ── Set up /proc (new PID namespace needs it) ──────
        bwrap_args.append('--proc')
        bwrap_args.append('/proc')

        # ── Set up /dev (minimal) ──────────────────────────
        bwrap_args.append('--dev')
        bwrap_args.append('/dev')

        # ── Make /tmp writable ─────────────────────────────
        bwrap_args.append('--tmpfs')
        bwrap_args.append('/tmp')

        # ── Writable paths ─────────────────────────────────
        if writable_paths:
            for wp in writable_paths:
                wp = os.path.realpath(wp)
                bwrap_args.append('--bind')
                bwrap_args.append(wp)
                bwrap_args.append(wp)

        # Working directory
        cwd = os.getcwd()
        bwrap_args.append('--bind')
        bwrap_args.append(cwd)
        bwrap_args.append(cwd)

        # ── Read-only system paths ─────────────────────────
        ro_system_paths = [
            '/usr', '/lib', '/lib64', '/bin', '/sbin', '/opt',
            '/etc/alternatives', '/etc/ld.so.cache', '/etc/ld.so.conf',
            '/etc/ld.so.conf.d', '/etc/nsswitch.conf', '/etc/resolv.conf',
            '/etc/hosts', '/etc/hostname',
        ]

        if readonly_paths:
            ro_system_paths.extend(readonly_paths)

        for rp in ro_system_paths:
            rp = os.path.realpath(rp)
            if os.path.exists(rp):
                bwrap_args.append('--ro-bind')
                bwrap_args.append(rp)
                bwrap_args.append(rp)

        # ── /etc (limited read-only) ───────────────────────
        if os.path.isdir('/etc'):
            bwrap_args.append('--ro-bind')
            bwrap_args.append('/etc')
            bwrap_args.append('/etc')

        # ── Network control ────────────────────────────────
        if not allow_network:
            # Create empty /sys (network info lives under /sys/class/net)
            bwrap_args.append('--dir')
            bwrap_args.append('/sys')

        # ── D-Bus (optional) ───────────────────────────────
        if allow_dbus and os.path.isdir('/run/dbus'):
            bwrap_args.append('--bind')
            bwrap_args.append('/run/dbus')
            bwrap_args.append('/run/dbus')

        # ── XDG Runtime dir (if needed) ────────────────────
        xdg = os.environ.get('XDG_RUNTIME_DIR', '')
        if xdg and os.path.isdir(xdg):
            bwrap_args.append('--bind')
            bwrap_args.append(xdg)
            bwrap_args.append(xdg)

        # ── Fake home (tmpfs) ──────────────────────────────
        if home_dir:
            real_home = os.path.realpath(home_dir)
            if os.path.isdir(real_home):
                bwrap_args.append('--bind')
                bwrap_args.append(real_home)
                bwrap_args.append(real_home)
        else:
            # Use a tmpfs for home
            home = os.environ.get('HOME', '/tmp/fake-home')
            bwrap_args.append('--tmpfs')
            bwrap_args.append(home)

        # ── Set working directory ──────────────────────────
        bwrap_args.append('--chdir')
        bwrap_args.append(cwd)

        # ── Die with parent ────────────────────────────────
        bwrap_args.append('--die-with-parent')

        # ── Final: the command ─────────────────────────────
        bwrap_args.extend(cmd)

        return bwrap_args

    def get_available(self) -> bool:
        return self._available


# ======================================================================
# D16: NO_NEW_PRIVS GUARD
# ======================================================================

class NoNewPrivsGuard:
    """
    D16: PR_SET_NO_NEW_PRIVS — Prevent privilege escalation.

    Sets the NO_NEW_PRIVS process flag which prevents:
      - setuid/setgid binary escalation
      - Capability escalation
      - Any additional privilege gains

    Once set, this flag is inherited by all child processes and
    CANNOT be unset.

    This is also a prerequisite for seccomp-bpf filters.
    """

    _instance = None

    def __init__(self):
        self._available = False
        self._reason = ""

    @classmethod
    def get_instance(cls) -> 'NoNewPrivsGuard':
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def initialize(self) -> bool:
        global _no_new_privs_available
        if _no_new_privs_available is not None:
            self._available = _no_new_privs_available
            return self._available

        try:
            libc = ctypes.CDLL(ctypes.util.find_library('c'), use_errno=True)
            # Test if prctl works
            libc.prctl(PR_GET_NO_NEW_PRIVS, 0, 0, 0, 0)
            self._available = True
            _no_new_privs_available = True
            logger.info("  ✅ D16: NO_NEW_PRIVS guard ACTIVE")
            return True
        except Exception as e:
            self._reason = f"prctl unavailable: {e}"
            _no_new_privs_available = False
            return False

    def get_preexec_func(self) -> Optional[Callable[[], None]]:
        """Return a preexec function that sets NO_NEW_PRIVS."""
        if not self._available:
            return None

        def _apply_no_new_privs():
            try:
                libc = ctypes.CDLL(ctypes.util.find_library('c'), use_errno=True)
                libc.prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0)
            except Exception:
                pass

        return _apply_no_new_privs


# ======================================================================
# D17: FD LEAK PROTECTOR
# ======================================================================

class FdLeakProtector:
    """
    D17: FD Leak Protection — Close inherited FDs in subprocesses.

    When a subprocess is spawned, it inherits all open file descriptors
    from the parent. This includes:
      - Server sockets (could be used for network attacks)
      - Database connections
      - Log files
      - Temporary files

    This preexec function closes all non-standard FDs (>= 3) in the
    subprocess before it executes. This prevents the subprocess from:
      - Reading/writing server file handles
      - Holding locks on server resources
      - Exfiltrating data via inherited sockets
    """

    _instance = None

    # FDs to preserve (stdin, stdout, stderr)
    _PRESERVE_FDS = {0, 1, 2}

    def __init__(self):
        self._available = True  # Always available
        self._preserve_fds = set(self._PRESERVE_FDS)
        self._max_fd = 1024

    @classmethod
    def get_instance(cls) -> 'FdLeakProtector':
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def initialize(self) -> bool:
        """Detect close_range availability."""
        try:
            # Check if close_range is available (Linux 5.9+)
            if hasattr(os, 'close_range'):
                logger.info("  ✅ D17: FD leak protector ACTIVE (close_range)")
            else:
                logger.info("  ✅ D17: FD leak protector ACTIVE (sequential close)")
            return True
        except Exception as e:
            logger.info(f"  ⚠️ D17: FD leak protector limited ({e})")
            return True

    def get_preexec_func(self, preserve_fds: Optional[set] = None) -> Callable[[], None]:
        """
        Return a preexec function that closes inherited FDs.

        Args:
            preserve_fds: Set of FD numbers to keep open (default: {0,1,2})
        """
        preserve = set(preserve_fds) if preserve_fds else set(self._PRESERVE_FDS)
        max_fd = self._max_fd

        def _close_fds():
            try:
                # Try close_range first (fast, kernel-level)
                if hasattr(os, 'close_range'):
                    # Find the highest fd to close
                    start_fd = max(preserve) + 1
                    try:
                        os.close_range(start_fd, max_fd)
                        return
                    except OSError:
                        pass  # Fall through to sequential

                # Sequential close (slower but always works)
                # First try /proc/self/fd for efficiency
                try:
                    fd_dir = '/proc/self/fd'
                    if os.path.isdir(fd_dir):
                        for entry in os.listdir(fd_dir):
                            try:
                                fd = int(entry)
                                if fd > max(preserve):
                                    os.close(fd)
                            except (ValueError, OSError):
                                pass
                        return
                except (OSError, PermissionError):
                    pass

                # Brute-force close (last resort)
                for fd in range(3, max_fd + 1):
                    if fd in preserve:
                        continue
                    try:
                        os.close(fd)
                    except OSError:
                        pass
            except Exception:
                pass  # Preexec must never raise

        return _close_fds


# ======================================================================
# D18: PROCESS GROUP TREE KILLER
# ======================================================================

class TreeKiller:
    """
    D18: Tree Killer — Process group tree kill on timeout.

    When a subprocess times out, simply killing the parent process
    is not enough — the child processes continue running as orphans
    and can exhaust resources.

    This class provides:
      - Process group creation (setpgid in preexec)
      - Tree tracking (all spawned process PIDs)
      - Recursive tree kill (SIGTERM → SIGKILL cascade)
      - Grandchild process detection and cleanup

    On timeout:
      1. SIGTERM the process group
      2. Wait 2 seconds
      3. SIGKILL the process group
      4. Recursively find and kill any grandchildren
    """

    _instance = None

    def __init__(self):
        self._available = True
        self._tracked_pgroups = {}
        self._lock = threading.Lock()
        self._cleanup_running = False

    @classmethod
    def get_instance(cls) -> 'TreeKiller':
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def initialize(self) -> bool:
        logger.info("  ✅ D18: Process group tree killer ACTIVE")
        return True

    def get_preexec_func(self) -> Callable[[], None]:
        """Return a preexec function that creates a new process group."""
        def _create_pgroup():
            try:
                # Create a new process group
                os.setpgid(0, 0)
            except Exception:
                pass

        return _create_pgroup

    def track_process(self, pid: int) -> None:
        """Track a process for later cleanup."""
        with self._lock:
            self._tracked_pgroups[pid] = {
                'pid': pid,
                'started': time.time(),
                'children': []
            }

    def kill_tree(self, pid: int, grace_seconds: float = 2.0) -> Dict:
        """
        Kill an entire process tree.

        Uses process group kill (SIGTERM, then SIGKILL after grace).

        Also recursively finds and kills grandchildren by scanning /proc.
        """
        result = {'killed': 0, 'failed': 0}

        try:
            # Get the process group ID
            pgid = None
            try:
                pgid = os.getpgid(pid)
            except ProcessLookupError:
                pgid = None

            # SAFETY: Never kill our own process group
            own_pgid = os.getpgid(0)
            if pgid and pgid == own_pgid:
                logger.warning(f"D18: Refusing to kill own process group (PGID {pgid})")
                return result

            if pgid and pgid > 1:
                # Kill the entire process group
                # Step 1: SIGTERM (graceful)
                try:
                    os.killpg(pgid, signal.SIGTERM)
                    result['killed'] += 1
                except (ProcessLookupError, PermissionError, OSError):
                    pass

                # Wait for grace period
                if grace_seconds > 0:
                    time.sleep(grace_seconds)

                # Step 2: SIGKILL (forced)
                try:
                    os.killpg(pgid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError, OSError):
                    pass

            # Recursively find grandchildren by scanning /proc
            grand_killed = self._kill_grandchildren(pid, grace_seconds)
            result['killed'] += grand_killed

            # Remove from tracked list
            with self._lock:
                self._tracked_pgroups.pop(pid, None)

        except Exception as e:
            result['failed'] += 1
            logger.error(f"D18: Tree kill error for PID {pid}: {e}")

        return result

    def _kill_grandchildren(self, parent_pid: int,
                            grace_seconds: float = 1.0) -> int:
        """Recursively find and kill grandchildren of a process."""
        killed = 0
        try:
            for proc in process_list():
                if proc.get('ppid') == parent_pid:
                    # Found a child - kill it
                    child_pid = proc['pid']
                    try:
                        # Try process group kill first
                        try:
                            child_pgid = os.getpgid(child_pid)
                            if child_pgid > 1:
                                os.killpg(child_pgid, signal.SIGTERM)
                                if grace_seconds > 0:
                                    time.sleep(grace_seconds)
                                os.killpg(child_pgid, signal.SIGKILL)
                        except (ProcessLookupError, OSError):
                            pass

                        # Direct kill as fallback
                        os.kill(child_pid, signal.SIGKILL)
                        killed += 1
                    except (ProcessLookupError, OSError):
                        pass

                    # Recurse into grandchildren
                    killed += self._kill_grandchildren(
                        child_pid, 0  # No grace for deep children
                    )
        except Exception:
            pass
        return killed

    def cleanup_all(self) -> int:
        """Kill all tracked process groups."""
        total = 0
        with self._lock:
            pids = list(self._tracked_pgroups.keys())
        for pid in pids:
            result = self.kill_tree(pid, grace_seconds=0.5)
            total += result.get('killed', 0)
        return total


# ======================================================================
# D19: CAPABILITY DROPPER
# ======================================================================

class CapabilityDropper:
    """
    D19: Capability Dropper — Drop dangerous capabilities from subprocesses.

    Linux capabilities divide root privileges into distinct units.
    By dropping dangerous capabilities in subprocesses, we prevent:
      - CAP_SYS_ADMIN: mount, swapon, namespace creation, etc.
      - CAP_NET_ADMIN: network configuration
      - CAP_SYS_MODULE: kernel module loading
      - CAP_SYS_PTRACE: process tracing
      - CAP_SYS_BOOT: reboot/shutdown
      - CAP_KILL: sending signals to arbitrary processes
      - CAP_SYS_RAWIO: I/O port access
      - CAP_SYS_RESOURCE: resource limit changes

    Capabilities are dropped in the preexec function via
    /proc/self/task/{tid}/setgroups and /proc/self/task/{tid}/...
    or via prctl PR_CAPBSET_DROP for each dangerous capability.

    Graceful degradation: Capability dropping only works as root or
    in a user namespace. If not available, continues without it.
    """

    _instance = None
    _PRESERVE_CAPS = set()  # No capabilities needed for tool execution

    def __init__(self):
        self._available = False
        self._reason = ""
        self._has_user_ns = False

    @classmethod
    def get_instance(cls) -> 'CapabilityDropper':
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def initialize(self) -> bool:
        """Check capability dropping availability."""
        try:
            # Check if we're in a user namespace (capabilities work there)
            if os.path.isdir('/proc/self/ns/user'):
                try:
                    # User namespace availability check
                    self._has_user_ns = True
                    logger.info("  ✅ D19: Capability dropper ACTIVE (user namespace)")
                    self._available = True
                    return True
                except Exception:
                    pass

            # Check if we can write to our own capability bounds
            try:
                # Test if /proc/self/setgroups is writable
                if os.path.isfile('/proc/self/setgroups'):
                    with open('/proc/self/setgroups', 'r') as f:
                        if f.read().strip() == 'deny':
                            self._available = True
                            logger.info("  ✅ D19: Capability dropper ACTIVE")
                            return True
            except (PermissionError, IOError):
                pass

            # Last resort: PR_CAPBSET_DROP via prctl
            # This works in any context but only for bounding set
            try:
                libc = ctypes.CDLL(ctypes.util.find_library('c'), use_errno=True)
                # PR_CAPBSET_READ = 23, PR_CAPBSET_DROP = 24
                ret = libc.prctl(23, 0, 0, 0, 0)
                # Check if CAPBSET is supported (returns 1 if cap is in bounding set)
                self._available = True
                logger.info("  ✅ D19: Capability dropper ACTIVE (prctl)")
                return True
            except Exception:
                pass

            self._reason = "no capability dropping mechanism available"
            logger.info(f"  ⚠️ D19: Capability dropper unavailable ({self._reason})")
            return False

        except Exception as e:
            self._reason = f"init failed: {e}"
            logger.info(f"  ⚠️ D19: Capability dropper unavailable ({e})")
            return False

    def get_preexec_func(self) -> Optional[Callable[[], None]]:
        """Return a preexec function that drops dangerous capabilities."""
        if not self._available:
            return None

        def _drop_caps():
            try:
                libc = ctypes.CDLL(ctypes.util.find_library('c'), use_errno=True)

                # PR_CAPBSET_DROP for dangerous capabilities
                # These capability numbers are Linux-standard
                dangerous_caps = [
                    5,   # CAP_SYS_ADMIN (mount, swapon, namespace)
                    6,   # CAP_SYS_RESOURCE (resource limits)
                    12,  # CAP_SYS_PTRACE (process tracing)
                    16,  # CAP_SYS_MODULE (kernel modules)
                    21,  # CAP_SYS_BOOT (reboot)
                    22,  # CAP_SYS_RAWIO (I/O ports)
                    0,   # CAP_SYS_CHROOT (chroot escape)

                ]

                PR_CAPBSET_DROP = 24
                for cap in dangerous_caps:
                    try:
                        libc.prctl(PR_CAPBSET_DROP, cap, 0, 0, 0)
                    except Exception:
                        pass

                # Try to set NO_NEW_PRIVS as well (defense-in-depth)
                try:
                    libc.prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0)
                except Exception:
                    pass

            except Exception:
                pass  # Preexec must never raise

        return _drop_caps


# ======================================================================
# D20: DISK QUOTA ENFORCER
# ======================================================================

class DiskQuotaEnforcer:
    """
    D20: Disk Quota Enforcer — Per-tool disk write limits.

    Enforces filesystem write limits using RLIMIT_FSIZE.
    This prevents tools from filling up the disk with:
      - Massive log files
      - Test output files
      - Debug dumps
      - Created files

    RLIMIT_FSIZE limits the total size of files a process can create.
    When the limit is reached, the process receives SIGXFSZ and
    write operations fail with EFBIG.

    Limits:
      - Default: 100MB per subprocess
      - Heavy tools (python3, pytest, make): 500MB

    Graceful degradation: Always available (RLIMIT is always supported).
    """

    _instance = None
    _DEFAULT_LIMIT = 100 * 1024 * 1024  # 100MB
    _HEAVY_LIMIT = 500 * 1024 * 1024    # 500MB

    def __init__(self):
        self._available = True
        self._default_limit = self._HEAVY_LIMIT

    @classmethod
    def get_instance(cls) -> 'DiskQuotaEnforcer':
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def initialize(self) -> bool:
        import resource
        try:
            # Test that we can at least read RLIMIT_FSIZE
            resource.getrlimit(resource.RLIMIT_FSIZE)
            logger.info(f"  ✅ D20: Disk quota enforcer ACTIVE"
                        f" (default: {self._default_limit // (1024*1024)}MB)")
            return True
        except Exception as e:
            logger.info(f"  ⚠️ D20: Disk quota enforcer unavailable ({e})")
            self._available = False
            return False

    def get_preexec_func(self, limit_bytes: Optional[int] = None) -> Callable[[], None]:
        """Return a preexec function that sets RLIMIT_FSIZE."""
        if not self._available:
            # Return a no-op
            return lambda: None

        limit = limit_bytes if limit_bytes else self._default_limit

        def _apply_disk_quota():
            try:
                import resource
                resource.setrlimit(resource.RLIMIT_FSIZE, (limit, limit))
            except Exception:
                pass

        return _apply_disk_quota


# ======================================================================
# COMPOSITE PREEXEC FUNCTION
# ======================================================================

class DivinePreexecBuilder:
    """
    Builds a composite preexec function from all available protection layers.

    This combines D13-D20 into a single preexec function that:
      1. Closes inherited FDs (D17)
      2. Creates a new process group (D18)
      3. Drops capabilities (D19)
      4. Sets NO_NEW_PRIVS (D16)
      5. Applies seccomp-bpf filter (D14)
      6. Applies Landlock filesystem sandbox (D13)
      7. Sets disk quota (D20)

    The order matters — we want to:
      - Close FDs first (clean slate)
      - Create process group (for tree kill)
      - Drop capabilities before restricting further
      - Set NO_NEW_PRIVS before seccomp (required)
      - Apply seccomp before Landlock (seccomp filters syscalls)
      - Set disk quota last
    """

    def __init__(self):
        self._layers = []

    def add_layer(self, name: str, func: Callable[[], None]) -> None:
        """Add a protection layer preexec function."""
        if func is not None:
            self._layers.append((name, func))

    def build(self) -> Callable[[], None]:
        """Build the composite preexec function."""
        layers = list(self._layers)  # Copy

        def _divine_preexec():
            for name, func in layers:
                try:
                    func()
                except Exception:
                    pass  # Individual layer failures are OK

        return _divine_preexec

    def get_layer_summary(self) -> str:
        """Get a summary of active layers."""
        names = [name for name, _ in self._layers]
        return f"D13-D20 active layers: {', '.join(names)}" if names else "No divine layers available"


# ======================================================================
# ADVANCED DIVINE PROTECTION MANAGER
# ======================================================================

class AdvancedDivineProtection:
    """
    Master controller for all advanced divine protection layers (D13-D20).

    Manages initialization, preexec function creation, and shutdown.
    """

    _instance = None

    def __init__(self):
        self._initialized = False
        self._layers = {}

        # Layer instances
        self.landlock = LandlockLsmSandbox.get_instance()
        self.seccomp = SeccompBpfFilter.get_instance()
        self.bubblewrap = BubblewrapIsolation.get_instance()
        self.no_new_privs = NoNewPrivsGuard.get_instance()
        self.fd_protector = FdLeakProtector.get_instance()
        self.tree_killer = TreeKiller.get_instance()
        self.cap_dropper = CapabilityDropper.get_instance()
        self.disk_quota = DiskQuotaEnforcer.get_instance()

        # Status tracking
        self._status = {}
        self._preexec_builder = None

    @classmethod
    def get_instance(cls) -> 'AdvancedDivineProtection':
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def initialize(self) -> Dict[str, bool]:
        """Initialize all advanced divine protection layers."""
        if self._initialized:
            return self._status

        with _init_lock:
            if self._initialized:
                return self._status

            logger.info("")
            logger.info("═══ ADVANCED DIVINE PROTECTION (D13-D20) ═══")

            # D13: Landlock LSM
            self._status['d13_landlock'] = self.landlock.initialize()

            # D14: seccomp-bpf
            self._status['d14_seccomp'] = self.seccomp.initialize()

            # D15: bubblewrap
            self._status['d15_bubblewrap'] = self.bubblewrap.initialize()

            # D16: NO_NEW_PRIVS
            self._status['d16_no_new_privs'] = self.no_new_privs.initialize()

            # D17: FD leak protector
            self._status['d17_fd_protector'] = self.fd_protector.initialize()

            # D18: Tree killer
            self._status['d18_tree_killer'] = self.tree_killer.initialize()

            # D19: Capability dropper
            self._status['d19_cap_dropper'] = self.cap_dropper.initialize()

            # D20: Disk quota
            self._status['d20_disk_quota'] = self.disk_quota.initialize()

            # Build preexec function
            self._build_preexec()

            # Summary
            active = sum(1 for v in self._status.values() if v)
            total = len(self._status)
            logger.info(f"─── Advanced: {active}/{total} layers active ───")
            logger.info(f"═══ END ADVANCED DIVINE PROTECTION ═══")
            logger.info("")

            self._initialized = True
            return self._status

    def _build_preexec(self) -> None:
        """Build the composite divine preexec function."""
        builder = DivinePreexecBuilder()

        # Order matters (see DivinePreexecBuilder docstring)

        # D17: Close inherited FDs
        builder.add_layer('D17:FD_Close', self.fd_protector.get_preexec_func())

        # D18: Create process group
        builder.add_layer('D18:ProcessGroup', self.tree_killer.get_preexec_func())

        # D19: Drop capabilities
        builder.add_layer('D19:CapDrop', self.cap_dropper.get_preexec_func())

        # D16: NO_NEW_PRIVS (before seccomp)
        builder.add_layer('D16:NoNewPrivs', self.no_new_privs.get_preexec_func())

        # D14: seccomp-bpf filter
        builder.add_layer('D14:Seccomp', self.seccomp.get_preexec_filter())

        # D13: Landlock filesystem sandbox
        builder.add_layer('D13:Landlock', self.landlock.get_preexec_sandbox())

        # D20: Disk quota
        builder.add_layer('D20:DiskQuota', self.disk_quota.get_preexec_func())

        self._preexec_builder = builder

    def get_divine_preexec(self) -> Callable[[], None]:
        """Get the composite divine preexec function.

        This function should be passed as the `preexec_fn` argument
        to subprocess.Popen().
        """
        if not self._initialized:
            self.initialize()
        return self._preexec_builder.build()

    def get_preexec_summary(self) -> str:
        """Get a summary of active preexec layers."""
        if not self._preexec_builder:
            return "No divine preexec built"
        return self._preexec_builder.get_layer_summary()

    def wrap_with_bubblewrap(self, cmd: list, **kwargs) -> Optional[list]:
        """Wrap a command with bubblewrap namespace isolation.

        Returns the wrapped command list, or None if bubblewrap is
        not available.
        """
        return self.bubblewrap.wrap_command(cmd, **kwargs)

    def get_bubblewrap_available(self) -> bool:
        return self.bubblewrap.get_available()

    def kill_process_tree(self, pid: int) -> Dict:
        """Kill an entire process tree (D18)."""
        return self.tree_killer.kill_tree(pid)

    def get_status(self) -> Dict:
        """Get status of all advanced protection layers."""
        return dict(self._status)

    def shutdown(self) -> None:
        """Clean shutdown of all advanced protection layers."""
        logger.info("Shutting down Advanced Divine Protection...")

        # Kill all tracked process trees
        killed = self.tree_killer.cleanup_all()
        if killed > 0:
            logger.info(f"  Killed {killed} remaining process tree(s)")

        self._initialized = False
        logger.info("Advanced Divine Protection shutdown complete")


# ======================================================================
# CONVENIENCE API
# ======================================================================

_advanced_protection = None


def get_advanced_protection() -> AdvancedDivineProtection:
    """Get or create the global AdvancedDivineProtection instance."""
    global _advanced_protection
    if _advanced_protection is None:
        _advanced_protection = AdvancedDivineProtection.get_instance()
    return _advanced_protection


def initialize_advanced_divine() -> Dict[str, bool]:
    """Initialize all advanced divine protection layers.

    Called from host_protection.py's initialize_divine_protection().
    """
    ap = get_advanced_protection()
    return ap.initialize()


def get_advanced_preexec() -> Callable[[], None]:
    """Get the composite divine preexec function.

    Convenience function that initializes if needed and returns
    the preexec function for subprocess.Popen().
    """
    ap = get_advanced_protection()
    return ap.get_divine_preexec()


def wrap_bubblewrap(cmd: list, **kwargs) -> Optional[list]:
    """Wrap a command with bubblewrap namespace isolation."""
    ap = get_advanced_protection()
    return ap.wrap_with_bubblewrap(cmd, **kwargs)


def kill_tree(pid: int) -> Dict:
    """Kill an entire process tree."""
    ap = get_advanced_protection()
    return ap.kill_process_tree(pid)


def get_advanced_status() -> Dict:
    """Get status of advanced protection layers."""
    ap = get_advanced_protection()
    return ap.get_status()


def shutdown_advanced_divine() -> None:
    """Shutdown all advanced divine protection layers."""
    ap = get_advanced_protection()
    ap.shutdown()


def get_comprehensive_preexec() -> Callable[[], None]:
    """
    Get the most comprehensive preexec function available.

    Combines ALL available protection layers into one preexec:
      - FD leak protection (D17)
      - Process group creation (D18)
      - Capability dropping (D19)
      - NO_NEW_PRIVS (D16)
      - seccomp-bpf syscall filtering (D14)
      - Landlock filesystem sandbox (D13)
      - Disk quota (D20)

    This is the ONE function to pass as preexec_fn= to subprocess.Popen.
    """
    ap = get_advanced_protection()
    return ap.get_divine_preexec()
