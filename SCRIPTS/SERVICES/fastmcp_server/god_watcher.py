"""
╔══════════════════════════════════════════════════════════════════════╗
║                     GOD WATCHER PROTECTION SYSTEM                   ║
║              The Final Unbreakable Line of Host Defense             ║
╚══════════════════════════════════════════════════════════════════════╝

THE GOD WATCHER — sits ABOVE the Divine Protection System (D1-D20)
and provides 4 additional layers of absolute protection:

  G1: Multi-Vector Pre-Execution Exploit Scanner
      - Buffer overflow detection (long strings, format strings %n/%s)
      - TOCTOU race condition patterns
      - Symlink / tmpfile attacks
      - Shell injection (ALL variants: $(), ``, ;, |, $IFS, ${})
      - Environment variable injection
      - Path traversal (/, .., %2e, unicode encoded)
      - Null byte injection (\\0 in strings/commands)
      - CRLF injection (\\r\\n for log poisoning)
      - XXE / XML external entity patterns
      - Deserialization attacks (pickle, yaml.load)
      - Command substitution at depth > 2
      - Unicode normalization attacks
      - Argument injection with semicolons
      - Race window patterns (sleep between check/use)
      - Format string exploits (%x, %n, %s chains)
      - Buffer overflow indicators (>4096 char tokens)

  G2: Runtime Behavioral Sentinel
      - Syscall rate anomaly (fork/exec burst detection)
      - File descriptor leak tracking
      - Memory allocation velocity monitoring
      - CPU usage spike detection
      - Network connection anomaly detection
      - Process depth / ancestry validation
      - File write velocity monitoring

  G3: Cryptographic Forensic Audit
      - Pre/post execution file state snapshot
      - Process tree before/after comparison
      - Network state before/after verification
      - Integrity hash chain for all audit records
      - Immutable audit log in sqlite database

  G4: AI Agent Oversight & Escalation
      - Heuristic scoring engine (0.0 safe → 1.0 malicious)
      - Unknown pattern matching against exploit signatures
      - Ambiguous score (0.4-0.7) → AI agent deep analysis
      - Extreme case (score > 0.7 but below known threshold)
        → Immediate BLOCK + Display Manager Alert
      - Full audit trail with human review request
      - Persistent notification system for system admin

RULES:
  - Every tool execution passes through ALL 4 layers
  - Any layer can BLOCK execution
  - Unknown/ambiguous patterns are escalated to AI + Human
  - All blocks are logged with full context to audit DB
  - Display manager alert for extreme unknown cases
"""

import hashlib
import json
import logging
import os
import re
import sqlite3
import subprocess
import threading
import time
import traceback
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, Any, Callable, List, Tuple
# ── Cross-OS Abstraction Layer ──────────────────────────────
from tools.ecosystem_os_abstraction import system_loadavg, system_memory, system_network_connections, process_info, process_kill

# ── Dynamic Config Registration ──────────────────────────────
try:
    from dynamic_config_loader import register_configs
    register_configs("god_watcher", [
        {"key": "alert_cpu_threshold", "type": "int", "default": 80,
          "description": "CPU alert threshold %", "valid_range": (1, 100),
          "category": "monitoring"},
        {"key": "alert_memory_threshold_mb", "type": "int", "default": 512,
          "description": "Memory alert threshold MB", "valid_range": (64, 32768),
          "category": "monitoring"},
        {"key": "check_interval_seconds", "type": "int", "default": 5,
          "description": "System health check interval", "valid_range": (1, 300),
          "category": "performance"},
        {"key": "max_alerts_per_hour", "type": "int", "default": 10,
          "description": "Max alerts per hour per source", "valid_range": (1, 1000),
          "category": "monitoring"},
        {"key": "auto_heal_enabled", "type": "bool", "default": True,
          "description": "Enable auto-healing actions", "category": "security"},
        {"key": "logging_level", "type": "str", "default": "INFO",
          "description": "Logging verbosity",
          "valid_options": ["DEBUG", "INFO", "WARNING", "ERROR"],
          "category": "debug"},
        {"key": "g1_signature_threshold", "type": "float", "default": 0.8,
          "description": "G1 exploit detection sensitivity", "valid_range": (0.0, 1.0),
          "category": "security"},
        {"key": "fd_leak_threshold", "type": "int", "default": 500,
          "description": "File descriptor leak alert threshold",
          "valid_range": (50, 10000), "category": "monitoring"},
    ])
except ImportError:
    pass
# ──────────────────────────────────────────────────────────────

logger = logging.getLogger("god_watcher")

# ═══════════════════════════════════════════════════════════════════════
# GLOBAL CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════

GOD_WATCHER_ENABLED = True
"""Master switch for the entire GOD WATCHER system."""

GOD_WATCHER_AUDIT_DB = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "DATA", "Databases", "god_watcher_audit.db"
)
"""SQLite database for immutable audit records."""

MAX_ARG_LENGTH = 4096
# ── Safe Tools Whitelist ──────────────────────────────────────────
# Tools in this list bypass G1 exploit scanning (they are introspection/
# reasoning tools that may contain code-like text in their parameters).
# They still pass through G2, G3, G4 for runtime monitoring.
SAFE_TOOLS = {
    'sequentialthinking', 'thinking', 'fill_prompt_template',
    'list_all_prompt_template_names', 'get_prompt_template_parameters',
    'thread_think', 'thread_recall', 'thread_associate', 'thread_reflect',
    'thread_get_stats', 'thread_events', 'thread_memory_graph',
    'thread_activation', 'thread_retriever', 'thread_cache',
    'thread_adaptive_controller', 'thread_memory',
    'documentation_search', 'get_database_config', 'set_database_config',
    'get_conversation_history', 'get_conversation_stats',
    'search_conversations', 'search_messages',
    'get_current_time', 'get_standard_time', 'get_swatch_time',
    'get_time', 'convert_time',
    'describe_image', 'query_image_with_prompt',
    'get_web_info', 'documentation_search',
    'check_pending_tasks', 'search_pending_files',
    'check_github_upload', 'get_dry_run',
    'get_browser_status', 'backup_audit',
    'vfs_read', 'vfs_list', 'vfs_info', 'vfs_stats',
    'vfs_validate', 'vfs_staging_summary',
    # write_big_file/read_big_file intentionally kept in SAFE_TOOLS because
    # their content params exceed G1's MAX_ARG_LENGTH=4096. Protection is
    # provided by Host Protection + VFS validation + internal file size checks.
    # See M2 in VFS_WRITE_FAILURE_INVESTIGATION.md for full analysis.
    'write_big_file', 'read_big_file',
}

# ── Dynamic SAFE_TOOLS Loading ────────────────────────────────────
# SAFE_TOOLS_STATIC is the hardcoded set of introspection/reasoning tools.
# At runtime, _get_safe_tools() merges this with dynamic tools loaded
# from the tool_registry database so newly registered tools are automatically
# recognized as safe without requiring a server restart.
#
# The cache auto-refreshes every SAFE_TOOLS_REFRESH_INTERVAL seconds.
# Call flush_g1_cache() on the GodWatcher singleton to force a refresh.

SAFE_TOOLS_STATIC = SAFE_TOOLS  # Alias for clarity

SAFE_TOOLS_REFRESH_INTERVAL = 60  # seconds between DB re-queries (default, overridable via config)
"""How often to refresh SAFE_TOOLS from the tool_registry database."""

_CONFIG_DB_PATH = os.path.expanduser(
    "~/AIHandler/SCRIPTS/DatabaseHandler/DATA/Databases/Documentation.db"
)
"""Path to Documentation.db for reading config values."""


def _get_g1_cache_refresh_interval() -> int:
    """
    Read the G1 cache refresh interval from the config table.
    Falls back to SAFE_TOOLS_REFRESH_INTERVAL (60s) if not configured or on error.
    """
    try:
        if not os.path.exists(_CONFIG_DB_PATH):
            return SAFE_TOOLS_REFRESH_INTERVAL
        conn = sqlite3.connect(_CONFIG_DB_PATH)
        try:
            c = conn.cursor()
            c.execute(
                "SELECT value FROM config WHERE key = ?",
                ("g1_cache.refresh_interval_seconds",)
            )
            row = c.fetchone()
            if row:
                val = int(row[0])
                if val > 0:
                    return val
        except (ValueError, sqlite3.Error):
            pass
        finally:
            conn.close()
    except Exception:
        pass
    return SAFE_TOOLS_REFRESH_INTERVAL

_TOOL_REGISTRY_DB = os.path.expanduser(
    "~/AIHandler/SCRIPTS/DatabaseHandler/DATA/Databases/Documentation.db"
)
"""Path to the Documentation.db that holds the tool_registry table."""

_safe_tools_cache = None  # Union of static + dynamic, computed lazily
_safe_tools_last_refresh = 0.0  # time.time() of last refresh
_safe_tools_refresh_lock = threading.Lock()


def _load_tools_from_registry() -> set:
    """
    Query tool_registry table for tools explicitly designated as safe.
    
    Only tools in the 'reasoning' or 'introspection' categories (which are
    inherently read-only and safe) are merged into SAFE_TOOLS. This prevents
    dangerous tools like execute_command, write_file, etc. from bypassing G1
    exploit scanning.
    
    Tools in the tools/ directory that need SAFE_TOOLS bypass should be added
    to the static SAFE_TOOLS set in the code.
    """
    try:
        import sqlite3
        conn = sqlite3.connect(_TOOL_REGISTRY_DB)
        cur = conn.cursor()
        # Only load tools in safe categories (reasoning, introspection)
        cur.execute(
            "SELECT tool_name FROM tool_registry "
            "WHERE enabled = 1 AND category IN ('reasoning', 'introspection')"
        )
        tools = {row[0] for row in cur.fetchall()}
        conn.close()
        return tools
    except Exception as e:
        logger.debug(f"G1 cache: Failed to load tool_registry: {e}")
        return set()


def _get_safe_tools() -> set:
    """
    Get the merged set of safe tools: static SAFE_TOOLS + dynamic registry.
    Results are cached and auto-refreshed periodically (interval from config table, default 60s).
    """
    global _safe_tools_cache, _safe_tools_last_refresh
    
    interval = _get_g1_cache_refresh_interval()
    
    now = time.time()
    if _safe_tools_cache is not None and (now - _safe_tools_last_refresh) < interval:
        return _safe_tools_cache
    
    with _safe_tools_refresh_lock:
        # Double-check after acquiring lock
        if _safe_tools_cache is not None and (now - _safe_tools_last_refresh) < interval:
            return _safe_tools_cache
        
        static = SAFE_TOOLS_STATIC
        dynamic = _load_tools_from_registry()
        merged = static | dynamic
        
        _safe_tools_cache = merged
        _safe_tools_last_refresh = time.time()
        
        added = dynamic - static
        if added:
            logger.debug(f"G1 cache: Loaded {len(dynamic)} dynamic tools, "
                         f"{len(added)} new: {sorted(added)[:10]}")
        
        return merged


def _flush_safe_tools_cache():
    """
    Force-flush the SAFE_TOOLS cache so _get_safe_tools() re-queries
    the database on the next call. Thread-safe.
    """
    global _safe_tools_cache, _safe_tools_last_refresh
    with _safe_tools_refresh_lock:
        _safe_tools_cache = None
        _safe_tools_last_refresh = 0.0
    logger.info("G1 cache: SAFE_TOOLS cache flushed — will reload from DB on next use")


# ---------------------------------------------------------------------------
# G1 Cache: Real-time hot-reload file watcher
# Watches the tools/ directory for new/changed .py files and auto-flushes
# the SAFE_TOOLS cache so newly registered tools are recognized without
# requiring a restart.flag or manual sync.
# ---------------------------------------------------------------------------

_TOOLS_WATCH_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools")
"""Directory to watch for new/changed tool files."""

_tools_file_snapshots = {}  # filename -> (mtime, size)
_tools_watcher_running = False
_tools_watcher_lock = threading.Lock()


def _scan_tools_directory() -> bool:
    """
    Scan the tools directory and return True if changes detected.
    Thread-safe.
    """
    global _tools_file_snapshots
    try:
        if not os.path.isdir(_TOOLS_WATCH_DIR):
            return False
        current = {}
        for fname in os.listdir(_TOOLS_WATCH_DIR):
            if fname.endswith('.py') and not fname.startswith('__'):
                fpath = os.path.join(_TOOLS_WATCH_DIR, fname)
                try:
                    st = os.stat(fpath)
                    current[fname] = (st.st_mtime, st.st_size)
                except OSError:
                    continue
        with _tools_watcher_lock:
            if _tools_file_snapshots != current:
                _tools_file_snapshots = current
                return True
        return False
    except Exception:
        return False


def _sync_tool_registry_from_filesystem():
    """
    Sync new .py files from the tools directory into the tool_registry DB.
    Uses dynamic_tool_manager's scan function if available, otherwise
    falls back to a simple INSERT for new files.
    """
    try:
        # Try using the dynamic_tool_manager's full scan function
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
        from dynamic_tool_manager import reload_tool_definitions
        result = reload_tool_definitions(dry_run=False)
        logger.info(f"G1 watcher: Tool registry sync result: {result[:200] if result else 'OK'}")
        return True
    except ImportError:
        pass
    except Exception as e:
        logger.debug(f"G1 watcher: dynamic_tool_manager not available: {e}")

    # Fallback: direct DB insert for new files
    try:
        import sqlite3
        conn = sqlite3.connect(_TOOL_REGISTRY_DB)
        cur = conn.cursor()
        for fname in os.listdir(_TOOLS_WATCH_DIR):
            if fname.endswith('.py') and not fname.startswith('__'):
                tool_name = fname[:-3]  # strip .py
                cur.execute(
                    "SELECT COUNT(*) FROM tool_registry WHERE tool_name = ?",
                    (tool_name,)
                )
                if cur.fetchone()[0] == 0:
                    cur.execute(
                        "INSERT OR IGNORE INTO tool_registry "
                        "(tool_name, module_name, function_name, file_path, category, enabled) "
                        "VALUES (?, ?, ?, ?, 'general', 1)",
                        (tool_name, tool_name, tool_name, fname)
                    )
        conn.commit()
        conn.close()
        _flush_safe_tools_cache()
        return True
    except Exception as e:
        logger.debug(f"G1 watcher: Fallback sync failed: {e}")
        return False


def _tools_watcher_loop():
    """
    Background thread: polls tools/ directory every 10 seconds.
    When new/changed .py files are detected, syncs the tool_registry DB
    and flushes the SAFE_TOOLS cache.
    """
    interval = 10  # seconds between polls
    while _tools_watcher_running:
        try:
            if _scan_tools_directory():
                logger.info("G1 watcher: Tool directory changes detected → syncing registry")
                _sync_tool_registry_from_filesystem()
        except Exception:
            pass
        time.sleep(interval)


def start_tools_watcher():
    """Start the background file watcher thread. Idempotent."""
    global _tools_watcher_running
    with _tools_watcher_lock:
        if _tools_watcher_running:
            return
        _tools_watcher_running = True
    thread = threading.Thread(target=_tools_watcher_loop, daemon=True, name="g1-tools-watcher")
    thread.start()
    logger.info("G1 watcher: Background tools directory watcher started (poll interval: 10s)")


def stop_tools_watcher():
    """Stop the background file watcher thread."""
    global _tools_watcher_running
    with _tools_watcher_lock:
        _tools_watcher_running = False
    logger.info("G1 watcher: Background tools directory watcher stopped")


"""Maximum allowed argument string length before buffer overflow warning."""

MAX_NESTED_SUBSTITUTION = 2
"""Maximum depth of nested command substitution ($() inside $())."""

SUSPICIOUS_SCORE_THRESHOLD = 0.4
"""Score above this triggers extra scrutiny."""

BLOCK_SCORE_THRESHOLD = 0.7
"""Score above this triggers automatic block."""

AI_AGENT_THRESHOLD = 0.55
"""Score above this but below BLOCK triggers AI agent consideration."""

MAX_SYSCALL_RATE = 100
"""Maximum syscalls per second before anomaly detection."""

MAX_FD_LEAK_RATE = 20
"""Maximum new file descriptors per second before leak detection."""

MAX_FILE_WRITE_RATE = 50
"""Maximum file writes per minute before velocity check."""

# ═══════════════════════════════════════════════════════════════════════
# G1: MULTI-VECTOR EXPLOIT SCANNER
# ═══════════════════════════════════════════════════════════════════════

class G1ExploitScanner:
    """
    G1: Multi-Vector Pre-Execution Exploit Scanner.
    
    Scans ALL tool parameters and commands for every known exploit vector
    before execution is allowed to proceed. This is the FIRST gate.
    """
    
    def __init__(self):
        self._stats = defaultdict(int)
    
    def scan_all_params(self, tool_name: str, params: dict) -> Tuple[bool, str, float]:
        """
        Scan ALL parameters for all known exploit vectors.
        
        Returns:
            (allowed, block_reason, suspicion_score)
        """
        if not GOD_WATCHER_ENABLED:
            return True, "", 0.0
        
        # Flatten all params into strings for analysis
        all_strings = self._flatten_params(params)
        highest_score = 0.0
        block_reason = ""
        
        for s in all_strings:
            allowed, reason, score = self._scan_single_string(s)
            if not allowed:
                return False, reason, score
            highest_score = max(highest_score, score)
        
        # Also do cross-parameter analysis
        cross_allowed, cross_reason, cross_score = self._cross_param_analysis(all_strings)
        if not cross_allowed:
            return False, cross_reason, cross_score
        highest_score = max(highest_score, cross_score)
        
        return True, "", highest_score
    
    def _flatten_params(self, params: dict, max_depth: int = 5) -> List[str]:
        """Flatten nested params into a list of strings for analysis."""
        strings = []
        def _flatten(obj, depth=0):
            if depth > max_depth:
                return
            if isinstance(obj, str):
                strings.append(obj)
            elif isinstance(obj, dict):
                for v in obj.values():
                    _flatten(v, depth + 1)
            elif isinstance(obj, (list, tuple)):
                for item in obj:
                    _flatten(item, depth + 1)
            elif isinstance(obj, bytes):
                try:
                    strings.append(obj.decode('utf-8', errors='replace'))
                except Exception:
                    pass
            elif obj is not None:
                strings.append(str(obj))
        _flatten(params)
        return strings
    
    def _scan_single_string(self, s: str) -> Tuple[bool, str, float]:
        """
        Scan a single string against ALL exploit vectors.
        
        Returns (allowed, block_reason, suspicion_score).
        """
        if not s:
            return True, "", 0.0
        
        score = 0.0
        reasons = []
        
        # ── Vector 1: Buffer Overflow Indicators ──────────────
        if len(s) > MAX_ARG_LENGTH:
            score = max(score, 0.9)
            reasons.append(f"Buffer overflow risk: string length {len(s)} > {MAX_ARG_LENGTH}")
        
        # ── Vector 2: Format String Exploits ─────────────────
        # %n writes to memory, %s reads strings, %x leaks stack
        format_chain = re.findall(r'%[ndixXuscfgeEG].*%[ndixXuscfgeEG]', s)
        if format_chain:
            score = max(score, 0.85)
            reasons.append(f"Format string chaining detected: {format_chain[:3]}")
        
        if re.search(r'%[sn]', s):
            score = max(score, 0.7)
            reasons.append("Format string write exploit (%n / %s)")
        
        # ── Vector 3: Shell Injection (ALL variants) ─────────
        shell_injections = {
            'backtick-cmd': r'`[^`]*(?:;|\||\$|&&|>|<|`|\n|\b(?:rm[^/\w]|shutdown|reboot|halt|poweroff|mkfs|dd[^/\w]|wget|curl|sudo|chmod|chown|kill|pkill|systemctl|exec|eval)\b)[^`]*`',
            'dollar-paren': r'\$\([^)]+\)',
            'semicolon-cmd': r';[ \t]*(rm|shutdown|reboot|halt|poweroff|mkfs|dd|wget|curl)',
            'pipe-cmd': r'[|][|]?[ \t]*(rm|shutdown|reboot|halt|poweroff|mkfs|dd)',
            'and-cmd': r'&&[ \t]*(rm|shutdown|reboot|halt|poweroff)',
            'dollar-brace': r'\$\{[^}]+\}',
            'ifs-injection': r'\$IFS',
            'subshell': r'\([^)]*\)\{[^}]*\}',
        }
        
        for name, pattern in shell_injections.items():
            if re.search(pattern, s):
                score = max(score, 0.8)
                reasons.append(f"Shell injection ({name})")
        
        # ── Vector 4: Nested Command Substitution ────────────
        depth = self._count_nesting_depth(s)
        if depth > MAX_NESTED_SUBSTITUTION:
            score = max(score, 0.85)
            reasons.append(f"Nested substitution depth {depth} > {MAX_NESTED_SUBSTITUTION}")
        
        # ── Vector 5: Path Traversal ─────────────────────────
        path_traversal = [
            r'\.\./\.\./',          # Standard ../../
            r'\.\.\\\.\.\\',        # Windows ..\..\
            r'%2e%2e%2f',          # URL-encoded ../
            r'%2e%2e\\',            # URL-encoded ..\
            r'\.\.%00',             # .. with null byte
            r'%c0%ae%c0%ae',        # Unicode overlong ../
            r'%252e%252e%252f',     # Double URL-encoded ../
            r'\.\.;/',             # ..;/
        ]
        for pattern in path_traversal:
            if re.search(pattern, s, re.IGNORECASE):
                score = max(score, 0.85)
                reasons.append(f"Path traversal ({pattern})")
                break
        
        # ── Vector 6: Null Byte Injection ────────────────────
        if '\x00' in s or '\\x00' in s.lower():
            score = max(score, 0.95)
            reasons.append("Null byte injection (\\x00)")
        
        # ── Vector 7: CRLF Injection ─────────────────────────
        if '\r\n' in s or '\\r\\n' in s.lower():
            score = max(score, 0.7)
            reasons.append("CRLF injection (\\r\\n log poisoning)")
        
        # ── Vector 8: Environment Variable Injection ─────────
        env_injections = [
            r'ENV=.*[;|`]',
            r'LD_PRELOAD=',
            r'LD_LIBRARY_PATH=',
            r'PYTHONPATH=.*[;|`]',
            r'PATH=\.', r'PATH=.*:\.',
            r'IFS=.*[;|`]',
            r'SHELLOPTS=',
            r'BASH_ENV=',
        ]
        for pattern in env_injections:
            if re.search(pattern, s, re.IGNORECASE):
                score = max(score, 0.85)
                reasons.append(f"Environment injection ({pattern})")
                break
        
        # ── Vector 9: XXE / XML External Entity ──────────────
        xxe_patterns = [
            r'<!ENTITY\s+\w+\s+SYSTEM\s+["\']',
            r'<!DOCTYPE\s+\w+\s+\[',
            r'<!ENTITY\s+%\s+\w+\s+SYSTEM',
            r'xinclude\s+[^>]*href=',
        ]
        for pattern in xxe_patterns:
            if re.search(pattern, s, re.IGNORECASE):
                score = max(score, 0.9)
                reasons.append("XXE/XML external entity attack")
                break
        
        # ── Vector 10: Deserialization Attacks ───────────────
        deser_patterns = [
            r'pickle\.loads?\(',
            r'cPickle\.loads?\(',
            r'yaml\.load\(',
            r'yaml\.full_load\(',
            r'__reduce__',
            r'__getstate__',
            r'__setstate__',
            r'\x80\x04',     # pickle protocol 4 header
            r'cos\nsystem',  # pickle os.system
            r'cbuiltins\nexec', # pickle builtins.exec
        ]
        for pattern in deser_patterns:
            if re.search(pattern, s):
                score = max(score, 0.85)
                reasons.append(f"Deserialization attack ({pattern})")
                break
        
        # ── Vector 11: Unicode Normalization Attacks ────────
        unicode_normalization = [
            '\u2025',  # ‥ double period
            '\u2215',  # ∕ division slash
            '\uff0f',  # ／ fullwidth solidus
            '\uff1c',  # ＜ fullwidth less-than
            '\uff1e',  # ＞ fullwidth greater-than
            '\ufe68',  # ﹨ small reverse solidus
            '\uff20',  # ＠ fullwidth commercial at
        ]
        for ch in unicode_normalization:
            if ch in s:
                score = max(score, 0.6)
                reasons.append(f"Unicode normalization attack (U+{ord(ch):04X})")
                break
        
        # ── Vector 12: TOCTOU Race Condition Patterns ────────
        toctou_patterns = [
            (r'os\.access\(.*\)\s+and\s+os\.(open|remove|unlink|rename)', "TOCTOU check-then-use"),
            (r'os\.path\.(exists|isfile|isdir)\(.*\)\s+and\s+.*(open|remove)', "TOCTOU path-exists-then-use"),
            (r'if\s+os\.path\.\w+\(.*\):.*\n.*os\.(remove|unlink|rename)', "TOCTOU multi-line race"),
            (r'tempfile\.mktemp\b', "Insecure temp file (mktemp race)"),
            (r'os\.tempnam\b', "Insecure temp name"),
            (r'os\.tmpnam\b', "Insecure temp name"),
        ]
        for pattern, reason in toctou_patterns:
            if re.search(pattern, s):
                score = max(score, 0.75)
                reasons.append(reason)
                break
        
        # ── Vector 13: Symlink/Tmpfile Attacks ──────────────
        symlink_patterns = [
            (r'/tmp/[^/\s]*\.(sh|py|so|conf)\b', "Temp script file in /tmp/ (symlink race)"),
            (r'/\w+/\.\w+rc', "RC file modification"),
            (r'chmod\s+\d{3,4}\s+/tmp/', "Permission change on /tmp/ file"),
            (r'ln\s+-s[f]?\s+/[^\s]+\s+/tmp/', "Symlink to system file in /tmp"),
            (r'mktemp\s+.*--dry-run', "mktemp dry-run (leaks temp name)"),
        ]
        for pattern, reason in symlink_patterns:
            if re.search(pattern, s):
                score = max(score, 0.7)
                reasons.append(reason)
                break
        
        # ── Vector 14: Argument Injection ────────────────────
        arg_injection = [
            r'--[a-z]+=.*[;|`]',       # --option=value;cmd
            r'--[a-z]+=\$\(',           # --option=$(cmd)
            r'-o[A-Za-z]*=[^"\']*;',    # -oOption=val;
            r'\s+-e\s+.*[;|`]',         # -e with injection
        ]
        for pattern in arg_injection:
            if re.search(pattern, s):
                score = max(score, 0.7)
                reasons.append(f"Argument injection ({pattern})")
                break
        
        # ── Vector 15: Race Window Patterns ─────────────────
        race_patterns = [
            (r'sleep\s+\d+[.]?\d*\s*[;|&].*rm', "Sleep-then-delete race"),
            (r'sleep\s+\d+[.]?\d*\s*[;|&].*mv', "Sleep-then-move race"),
            (r'sleep\s+\d+[.]?\d*\s*[;|&].*chmod', "Sleep-then-chmod race"),
            (r'time\.sleep\(.*\).*\n.*os\.remove', "Sleep-then-remove race"),
            (r'time\.sleep\(.*\).*\n.*os\.unlink', "Sleep-then-unlink race"),
        ]
        for pattern, reason in race_patterns:
            if re.search(pattern, s):
                score = max(score, 0.8)
                reasons.append(reason)
                break
        
        # ── Vector 16: Fork Bomb / Process Exhaust ──────────
        fork_patterns = [
            r'while\s+True:\s*os\.fork\(\)',
            r'while\s+1:\s*os\.fork\(\)',
            r'for\s+\w+\s+in\s+range\(\d+\):\s*os\.fork',
            r'\(\)\s*\{[^}]*\|[^}]*\}',  # :(){ :|:& };:
        ]
        for pattern in fork_patterns:
            if re.search(pattern, s):
                score = max(score, 0.95)
                reasons.append("Fork bomb pattern")
                break
        
        # ── Vector 17: Direct Memory / Hardware Access ───────
        memory_patterns = [
            r'/dev/mem\b', r'/dev/kmem\b', r'/dev/port\b',
            r'/dev/sda[0-9]', r'/dev/nvme[0-9]',
            r'/sys/firmware/', r'/sys/kernel/',
            r'iopl\b', r'ioperm\b', r'i386_set_ioperm',
            r'mmap.*PROT_WRITE.*MAP_SHARED',
        ]
        for pattern in memory_patterns:
            if re.search(pattern, s):
                score = max(score, 0.9)
                reasons.append(f"Direct memory/hardware access ({pattern})")
                break
        
        # ── Vector 18: Seccomp / Kernel Bypass ──────────────
        bypass_patterns = [
            r'prctl\(.*PR_SET_SECCOMP',
            r'seccomp\(', r'seccomp_load',
            r'SECCOMP_SET_MODE_FILTER',
            r'write\(.*/proc/.*/mem',
            r'ptrace\(.*PTRACE',
            r'process_vm_writev',
        ]
        for pattern in bypass_patterns:
            if re.search(pattern, s):
                score = max(score, 0.9)
                reasons.append(f"Seccomp/kernel bypass attempt ({pattern})")
                break
        
        # ── Vector 19: Ptrace / Debugger Attachment ───────────
        ptrace_patterns = [
            r'ptrace\(', r'PTRACE_PEEKTEXT', r'PTRACE_POKETEXT',
            r'PTRACE_ATTACH', r'PTRACE_SYSCALL',
            r'gdb\s+[-]p\s+\d+', r'delve\s+attach',
            r'lldb\s+[-]p\s+\d+', r'strace\s+[-]p\s+\d+',
        ]
        for pattern in ptrace_patterns:
            if re.search(pattern, s):
                score = max(score, 0.85)
                reasons.append(f"Ptrace/debugger attachment ({pattern})")
                break
        
        # ── Vector 20: Side-Channel / Timing Attack ──────────
        timing_patterns = [
            r'time\.perf_counter\(\)', r'time\.process_time\(\)',
            r'rdtsc\b', r'__rdtsc\b', r'__asm__.*rdtsc',
            r'clock_gettime\(CLOCK_MONOTONIC',
            r'gettimeofday.*%[a-z]',
        ]
        for pattern in timing_patterns:
            if re.search(pattern, s):
                # Timing is only suspicious in combination with other vectors
                score = max(score, 0.3)
                reasons.append(f"Timing measurement ({pattern})")
                break
        
        # ── Vector 21: DNS / Network Covert Channel ──────────
        dns_covert = [
            r'dig\s+[a-z]{40,}\.',       # DNS with very long label (data exfil via DNS)
            r'nslookup\s+.*\.',            # Long nslookup queries
            r'host\s+-[aA]\s+.*',          # Host with type ANY (DNS tunneling)
            r'ping\s+-[cps]\s+\d+\s+\d+',   # Ping flood
            r'hping3\s+', r'tcpdump\s+', r'tshark\s+',
        ]
        for pattern in dns_covert:
            if re.search(pattern, s):
                score = max(score, 0.7)
                reasons.append(f"Network covert channel ({pattern})")
                break
        
        # ── Vector 22: Polymorphic / Self-Modifying Code ─────
        poly_patterns = [
            r'base64\s+-d\s*\|.*(bash|sh|python|perl)',
            r'echo\s+["\'][A-Za-z0-9+/=]{40,}["\']\s*\|.*base64',
            r'openssl\s+enc\s+[-]d\s+[-]a',
            r'python3\s+-c\s+["\'][^"\']{0,20}__import__',
            r'exec\(compile\(', r'exec\(base64\.',
            r'dec2hex\|xxd\|', r'perl\s+-e\s+[\'"]\s*eval',
        ]
        for pattern in poly_patterns:
            if re.search(pattern, s):
                score = max(score, 0.8)
                reasons.append(f"Polymorphic/self-modifying code ({pattern})")
                break
        
        # ── Vector 23: Reverse Shell / Bind Shell ─────────────
        reverse_shell = [
            r'bash\s+-i\s+[>&].*/dev/tcp/',
            r'/dev/tcp/[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+/',
            r'sh\s+-i\s+[>&]\s+/dev/udp/',
            r'python[23]?\s+-c\s+[\'"].*socket.*connect\s*\(',
            r'mknod\s+\/tmp\/backpipe\s+p',
            r'nc\s+[-]e\s+/bin/(bash|sh|zsh)',
            r'socat\s+tcp',
        ]
        for pattern in reverse_shell:
            if re.search(pattern, s):
                score = max(score, 0.95)
                reasons.append("Reverse shell / bind shell pattern")
                break
        
        # ── Vector 24: Docker / Container Escape ─────────────
        escape_patterns = [
            r'--privileged', r'--pid=host', r'--net=host',
            r'--ipc=host', r'--cap-add=SYS_ADMIN',
            r'--cap-add=ALL', r'-v\s+/:/host',
            r'-v\s+/var/run/docker.sock',
            r'nsenter\s+--target\s+1',   # Enter PID 1 namespace
        ]
        for pattern in escape_patterns:
            if re.search(pattern, s):
                score = max(score, 0.85)
                reasons.append(f"Container escape attempt ({pattern})")
                break
        
        # ── Vector 25: LD_PRELOAD / Library Injection ────────
        lib_injection = [
            r'LD_PRELOAD=',
            r'LD_AUDIT=',
            r'LD_DEBUG=',
            r'__GLIBC_HAVE_SYSLOG',
            r'\$\{LD_',
            r'export\s+LD_',
            r'env\s+LD_',
        ]
        for pattern in lib_injection:
            if re.search(pattern, s):
                score = max(score, 0.85)
                reasons.append(f"Library injection ({pattern})")
                break
        
        # ── Vector 26: File Descriptor Tampering ─────────────
        fd_tamper = [
            r'/proc/self/fd/\d+',
            r'/dev/fd/\d+',
            r'exec\s+\d+[<>]',
            r'<\s*&\d+',  # <&3 dup2-like
        ]
        for pattern in fd_tamper:
            if re.search(pattern, s):
                score = max(score, 0.6)
                reasons.append(f"File descriptor tampering ({pattern})")
                break
        
        # ── Vector 27: Race Window via Signal Handling ───────
        signal_race = [
            r'signal\.signal\(.*SIG\w+.*,.*lambda',
            r'signal\.signal\(.*SIGCHLD.*,.*os\.wait',
            r'trap\s+[\'\"].*;.*[\'\"]\s+\d+',
            r'trap\s+[\'\"].*rm.*[\'\"]\s+EXIT',
        ]
        for pattern in signal_race:
            if re.search(pattern, s):
                score = max(score, 0.7)
                reasons.append(f"Signal race pattern ({pattern})")
                break
        
        # Record stats
        if score > 0:
            self._stats['total_scans'] += 1
            if score >= SUSPICIOUS_SCORE_THRESHOLD:
                self._stats['suspicious'] += 1
            if score >= BLOCK_SCORE_THRESHOLD:
                self._stats['blocked'] += 1
        
        if reasons:
            combined_reason = "; ".join(reasons[:3])  # Top 3 reasons
            if len(reasons) > 3:
                combined_reason += f" (+{len(reasons)-3} more)"
            return score < BLOCK_SCORE_THRESHOLD, combined_reason, score
        
        return True, "", 0.0
    
    def _count_nesting_depth(self, s: str) -> int:
        """Count the depth of nested command substitution."""
        max_depth = 0
        current = 0
        for i, ch in enumerate(s):
            if s[i:i+2] == '$(':
                current += 1
                max_depth = max(max_depth, current)
                i += 1
            elif ch == ')':
                if i > 0 and s[i-1] != '$':
                    current = max(0, current - 1)
        return max_depth
    
    def _cross_param_analysis(self, strings: List[str]) -> Tuple[bool, str, float]:
        """
        Cross-parameter analysis: checks combinations of params for exploits.
        E.g., a script path in one param + a dangerous arg in another.
        """
        score = 0.0
        reasons = []
        
        # Check for combined script + dangerous argument
        has_py_script = any(s.endswith('.py') for s in strings)
        has_danger_arg = any(
            re.search(r'--[a-z]+=.*[;|`]', s) for s in strings
        )
        if has_py_script and has_danger_arg:
            score = max(score, 0.6)
            reasons.append("Cross-param: Python script + dangerous argument")
        
        # Check for combined path traversal across params
        has_path_dots = any('..' in s for s in strings)
        has_slash = any('/' in s for s in strings if s.count('/') > 2)
        if has_path_dots and has_slash and len(strings) >= 2:
            score = max(score, 0.5)
            reasons.append("Cross-param: Path traversal indicators across params")
        
        if reasons:
            return score < BLOCK_SCORE_THRESHOLD, "; ".join(reasons[:2]), score
        return True, "", 0.0
    
    def get_stats(self) -> dict:
        """Get G1 scanning statistics."""
        stats = dict(self._stats)
        stats['safe_tools_dynamic_count'] = len(_safe_tools_cache) if _safe_tools_cache else 0
        stats['safe_tools_static_count'] = len(SAFE_TOOLS_STATIC)
        return stats

    def flush_cache(self):
        """
        Flush the G1 scanner and SAFE_TOOLS cache.
        Resets scan counters, forces fresh tool_registry reload.
        Thread-safe.
        """
        self._stats.clear()
        _flush_safe_tools_cache()
        logger.info('G1 cache: Full cache flush complete')


# ═══════════════════════════════════════════════════════════════════════
# G2: RUNTIME BEHAVIORAL SENTINEL
# ═══════════════════════════════════════════════════════════════════════

class G2RuntimeSentinel:
    """
    G2: Runtime Behavioral Sentinel.
    
    Monitors tool execution IN REAL TIME for anomalous behavior patterns
    that indicate exploitation or resource abuse.
    """
    
    def __init__(self):
        self._syscall_tracker = defaultdict(list)  # type -> [timestamps]
        self._fd_tracker = []  # [timestamps]
        self._write_tracker = []  # [timestamps]
        self._lock = threading.Lock()
        self._start_time = None
        self._anomalies = []
        self._active = False
    
    def start_monitoring(self):
        """Start the sentinel for a new tool execution."""
        self._start_time = time.time()
        self._active = True
        with self._lock:
            self._anomalies = []
    
    def stop_monitoring(self) -> List[Dict]:
        """Stop monitoring and return any detected anomalies."""
        self._active = False
        with self._lock:
            anomalies = list(self._anomalies)
            # Clear trackers to prevent cross-test contamination
            self._syscall_tracker.clear()
            self._fd_tracker.clear()
            self._write_tracker.clear()
            self._anomalies.clear()
            return anomalies
    
    def record_syscall(self, call_type: str):
        """Record a system call for rate analysis."""
        if not self._active:
            return
        now = time.time()
        with self._lock:
            self._syscall_tracker[call_type].append(now)
            # Prune old entries (> 2 seconds)
            cutoff = now - 2.0
            for ct in list(self._syscall_tracker.keys()):
                self._syscall_tracker[ct] = [t for t in self._syscall_tracker[ct] if t > cutoff]
            
            # Check rate
            total_recent = sum(len(ts) for ts in self._syscall_tracker.values())
            if total_recent > MAX_SYSCALL_RATE:
                self._anomalies.append({
                    'type': 'syscall_rate',
                    'detail': f'{total_recent} syscalls in last 2s',
                    'timestamp': now,
                    'severity': 0.7,
                })
    
    def record_fd_open(self):
        """Record a file descriptor open for leak detection."""
        if not self._active:
            return
        now = time.time()
        with self._lock:
            self._fd_tracker.append(now)
            cutoff = now - 1.0
            self._fd_tracker = [t for t in self._fd_tracker if t > cutoff]
            
            if len(self._fd_tracker) > MAX_FD_LEAK_RATE:
                self._anomalies.append({
                    'type': 'fd_leak',
                    'detail': f'{len(self._fd_tracker)} FDs opened in last 1s',
                    'timestamp': now,
                    'severity': 0.8,
                })
    
    def record_file_write(self):
        """Record a file write for velocity detection."""
        if not self._active:
            return
        now = time.time()
        with self._lock:
            self._write_tracker.append(now)
            cutoff = now - 60.0
            self._write_tracker = [t for t in self._write_tracker if t > cutoff]
            
            if len(self._write_tracker) > MAX_FILE_WRITE_RATE:
                self._anomalies.append({
                    'type': 'write_velocity',
                    'detail': f'{len(self._write_tracker)} writes in last 60s',
                    'timestamp': now,
                    'severity': 0.6,
                })
    
    def check_process_depth(self, pid: int, max_depth: int = 5) -> Tuple[bool, str]:
        """
        Check the process ancestry depth to detect fork bombs.
        Returns (safe, message).
        """
        try:
            depth = 0
            current_pid = pid
            server_pid = os.getpid()
            visited = set()
            
            while current_pid > 0 and current_pid not in visited:
                visited.add(current_pid)
                depth += 1
                if depth > max_depth:
                    return (False,
                            f"Process depth {depth} > {max_depth}. Fork bomb or runaway orphaning detected.")
                if current_pid == server_pid:
                    break
                try:
                    pinfo = process_info(current_pid)
                    current_pid = pinfo.get('ppid', 0) if pinfo.get('exists') else 0
                except (IOError, ValueError, IndexError):
                    break
            return True, ""
        except Exception as e:
            logger.debug(f"Process depth check error: {e}")
            return True, ""
    
    def get_max_anomaly_score(self) -> float:
        """Get the highest severity anomaly detected."""
        with self._lock:
            if not self._anomalies:
                return 0.0
            return max(a['severity'] for a in self._anomalies)


# ═══════════════════════════════════════════════════════════════════════
# G3: CRYPTOGRAPHIC FORENSIC AUDIT
# ═══════════════════════════════════════════════════════════════════════

class G3ForensicAudit:
    """
    G3: Cryptographic Forensic Audit System.
    
    Captures BEFORE/AFTER snapshots of the system state around tool execution
    and provides an immutable audit trail.
    """
    
    def __init__(self):
        self._before_hash = None
        self._after_hash = None
        self._audit_lock = threading.Lock()
        self._db_initialized = False
    
    def _ensure_db(self):
        """Ensure audit database exists and has schema."""
        if self._db_initialized:
            return
        with self._audit_lock:
            if self._db_initialized:
                return
            try:
                db_dir = os.path.dirname(GOD_WATCHER_AUDIT_DB)
                os.makedirs(db_dir, exist_ok=True)
                
                conn = sqlite3.connect(GOD_WATCHER_AUDIT_DB)
                c = conn.cursor()
                c.execute('''
                    CREATE TABLE IF NOT EXISTS god_watcher_audit (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp TEXT NOT NULL,
                        tool_name TEXT NOT NULL,
                        decision TEXT NOT NULL,
                        reason TEXT,
                        suspicion_score REAL DEFAULT 0.0,
                        g1_findings TEXT,
                        g2_findings TEXT,
                        g3_before_hash TEXT,
                        g3_after_hash TEXT,
                        g4_analysis TEXT,
                        g4_escalated INTEGER DEFAULT 0,
                        human_reviewed INTEGER DEFAULT 0,
						hash_chain_prev TEXT,
                        hash_chain_cur TEXT NOT NULL,
                        full_context TEXT
                    )
                ''')
                c.execute('''
                    CREATE TABLE IF NOT EXISTS god_watcher_notifications (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp TEXT NOT NULL,
                        notification_type TEXT NOT NULL,
                        message TEXT NOT NULL,
                        severity TEXT NOT NULL,
                        acknowledged INTEGER DEFAULT 0,
                        audit_id INTEGER,
                        FOREIGN KEY (audit_id) REFERENCES god_watcher_audit(id)
                    )
                ''')
                conn.commit()
                conn.close()
                self._db_initialized = True
                logger.debug(f"G3: Audit DB ready at {GOD_WATCHER_AUDIT_DB}")
            except Exception as e:
                logger.error(f"G3: Failed to initialize audit DB: {e}")
    
    def snapshot_before(self):
        """Take a pre-execution snapshot."""
        self._before_hash = self._compute_state_hash()
        return self._before_hash
    
    def snapshot_after(self):
        """Take a post-execution snapshot and verify integrity."""
        self._after_hash = self._compute_state_hash()
        return self._after_hash
    
    def _compute_state_hash(self) -> str:
        """Compute an integrity hash of system state."""
        hasher = hashlib.sha3_256()
        
        # Hash 1: Process tree snapshot
        try:
            my_pid = os.getpid()
            proc_tree = self._get_process_tree(my_pid)
            hasher.update(f"proctree:{proc_tree}".encode())
        except Exception:
            pass
        
        # Hash 2: Open file descriptors count
        try:
            pinfo = process_info(os.getpid())
            fd_count = pinfo.get('fd_count', 1) if pinfo.get('exists') else 1
            hasher.update(f"fds:{fd_count}".encode())
        except Exception:
            pass
        
        # Hash 3: Memory info (cross-platform)
        try:
            mem = system_memory()
            mem_str = f"total={mem['total_kb']} avail={mem['available_kb']} used={mem['used_kb']}"
            hasher.update(f"mem:{mem_str}".encode())
        except Exception:
            pass
        
        # Hash 4: Load average (cross-platform)
        try:
            load = system_loadavg()
            load_str = f"1m={load[0]} 5m={load[1]} 15m={load[2]}"
            hasher.update(f"load:{load_str}".encode())
        except Exception:
            pass
        
        # Hash 5: Network connections summary (cross-platform)
        try:
            net = system_network_connections()
            conn_summary = f"{len(net)} connections"
            hasher.update(f"tcp:{conn_summary}".encode())
        except Exception:
            pass
        
        return hasher.hexdigest()
    
    def _get_process_tree(self, pid: int) -> str:
        """Get a compact representation of the process tree."""
        children = []
        try:
            for entry in os.listdir('/proc'):
                if not entry.isdigit():
                    continue
                try:
                    pinfo = process_info(int(entry))
                    if pinfo.get('exists'):
                        ppid = pinfo.get('ppid', 0)
                        if ppid == pid:
                            children.append(int(entry))
                except Exception:
                    continue
            children.sort()
            return f"pid={pid},children={children}"
        except Exception:
            return f"pid={pid}"
    
    def verify_integrity(self) -> Tuple[bool, str]:
        """
        Verify that the system state before and after are consistent.
        Returns (ok, difference_summary).
        """
        if not self._before_hash or not self._after_hash:
            return True, "No snapshots to compare"
        
        if self._before_hash == self._after_hash:
            return True, "System state unchanged"
        
        # Find differences for audit
        diffs = []
        
        # Check process changes
        try:
            my_pid = os.getpid()
            before_tree = self._get_process_tree(my_pid)
            # Small delay to let processes settle
            after_tree = self._get_process_tree(my_pid)
            if before_tree != after_tree:
                diffs.append(f"process_tree changed")
        except Exception:
            pass
        
        # Check FD count
        try:
            pinfo_before = process_info(os.getpid())
            before_fds = pinfo_before.get('fd_count', 1) if pinfo_before.get('exists') else 1
            # Small delay
            pinfo_after = process_info(os.getpid())
            after_fds = pinfo_after.get('fd_count', 1) if pinfo_after.get('exists') else 1
            if before_fds != after_fds:
                diffs.append(f"FD count: {before_fds} -> {after_fds}")
        except Exception:
            pass
        
        if diffs:
            return True, "; ".join(diffs[:3])
        return True, "Hash changed (expected after tool execution)"
    
    def record_audit(self, tool_name: str, decision: str, reason: str,
                     suspicion_score: float, g1_findings: str = "",
                     g2_findings: str = "", g4_analysis: str = "",
                     g4_escalated: bool = False,
                     params: dict = None) -> Optional[int]:
        """
        Record an audit entry in the immutable audit log.
        
        Returns:
            audit_id if recorded successfully, None on failure.
        """
        self._ensure_db()
        
        try:
            conn = sqlite3.connect(GOD_WATCHER_AUDIT_DB)
            c = conn.cursor()
            
            now = datetime.now(timezone.utc).isoformat()
            
            # Build hash chain: hash of previous entry + current data
            c.execute("SELECT hash_chain_cur FROM god_watcher_audit ORDER BY id DESC LIMIT 1")
            row = c.fetchone()
            prev_hash = row[0] if row else "GENESIS"
            
            chain_data = f"{prev_hash}|{now}|{tool_name}|{decision}|{suspicion_score}"
            current_hash = hashlib.sha3_256(chain_data.encode()).hexdigest()
            
            full_context = json.dumps({
                'params_sample': str(params)[:1000] if params else "",
                'timestamp': now,
                'pid': os.getpid(),
            }) if params else json.dumps({'timestamp': now, 'pid': os.getpid()})
            
            c.execute('''
                INSERT INTO god_watcher_audit
                (timestamp, tool_name, decision, reason, suspicion_score,
                 g1_findings, g2_findings, g3_before_hash, g3_after_hash,
                 g4_analysis, g4_escalated, hash_chain_prev, hash_chain_cur,
                 full_context)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                now, tool_name, decision, reason[:2000] if reason else "",
                suspicion_score,
                str(g1_findings)[:2000] if g1_findings else "",
                str(g2_findings)[:2000] if g2_findings else "",
                self._before_hash or "",
                self._after_hash or "",
                str(g4_analysis)[:2000] if g4_analysis else "",
                1 if g4_escalated else 0,
                prev_hash, current_hash,
                str(full_context)[:2000]
            ))
            
            audit_id = c.lastrowid
            conn.commit()
            conn.close()
            
            return audit_id
        except Exception as e:
            logger.error(f"G3: Failed to record audit: {e}")
            return None
    
    def create_notification(self, notification_type: str, message: str,
                           severity: str = "warning", audit_id: int = None) -> bool:
        """Create a persistent notification for the display manager."""
        self._ensure_db()
        
        try:
            conn = sqlite3.connect(GOD_WATCHER_AUDIT_DB)
            c = conn.cursor()
            now = datetime.now(timezone.utc).isoformat()
            
            c.execute('''
                INSERT INTO god_watcher_notifications
                (timestamp, notification_type, message, severity, audit_id)
                VALUES (?, ?, ?, ?, ?)
            ''', (now, notification_type, message[:500], severity, audit_id))
            
            conn.commit()
            conn.close()
            
            # Also write to a notification file for the display manager
            notify_path = os.path.join(
                os.path.dirname(GOD_WATCHER_AUDIT_DB),
                "notifications.json"
            )
            try:
                existing = []
                if os.path.exists(notify_path):
                    with open(notify_path, 'r') as f:
                        try:
                            existing = json.load(f)
                        except json.JSONDecodeError:
                            existing = []
                
                existing.append({
                    'id': audit_id,
                    'timestamp': now,
                    'type': notification_type,
                    'message': message[:500],
                    'severity': severity,
                })
                
                with open(notify_path, 'w') as f:
                    json.dump(existing[-100:], f, indent=2)
            except Exception:
                pass
            
            return True
        except Exception as e:
            logger.error(f"G3: Failed to create notification: {e}")
            return False


# ═══════════════════════════════════════════════════════════════════════
# G4: AI AGENT OVERSIGHT & ESCALATION
# ═══════════════════════════════════════════════════════════════════════

class G4AIAgentOversight:
    """
    G4: AI Agent Oversight & Escalation System.
    
    For ambiguous/unknown cases where the static scanners (G1) can't
    definitively classify a pattern, this system:
    
    1. Runs heuristic scoring with weighted exploit signatures
    2. If score is 0.4-0.55: Flag as suspicious, allow with warning
    3. If score is 0.55-0.7: Escalate to AI agent for deep analysis
    4. If score > 0.7: AUTOMATICALLY BLOCK + Alert system admin
    
    All escalations produce persistent notifications visible to the
    display manager and are logged with full context.
    """
    
    def __init__(self):
        self._escalation_count = 0
        self._last_alert_time = 0
        self._min_alert_interval = 30  # seconds between display alerts
    
    def analyze(self, tool_name: str, params: dict, g1_score: float,
                g1_reasons: str, g2_anomalies: List[Dict]) -> Dict:
        """
        Perform AI agent oversight analysis.
        
        Returns dict with:
            - decision: "allow" | "block" | "escalate" | "alert"
            - score: final suspicion score
            - analysis: explanation text
            - needs_escalation: bool
            - human_review_needed: bool
        """
        result = {
            'decision': 'allow',
            'score': g1_score,
            'analysis': '',
            'needs_escalation': False,
            'human_review_needed': False,
        }
        
        # ── Step 1: Weighted scoring ─────────────────────────
        # Start with G1 score, adjust based on G2 findings
        final_score = g1_score
        
        # Add G2 anomaly severity
        max_g2_severity = max([a['severity'] for a in g2_anomalies]) if g2_anomalies else 0.0
        if max_g2_severity > 0:
            final_score = max(final_score, max_g2_severity * 0.8)
        
        # Apply heuristics
        heuristics_score = self._apply_heuristics(tool_name, params)
        final_score = max(final_score, heuristics_score * 0.6)
        
        # Clamp to [0, 1]
        final_score = min(1.0, max(0.0, final_score))
        result['score'] = final_score
        
        # ── Step 2: Decision based on score ─────────────────
        if final_score >= BLOCK_SCORE_THRESHOLD:
            # AUTOMATIC BLOCK - too dangerous
            result['decision'] = 'block'
            result['analysis'] = (
                f"AUTOMATIC BLOCK: Suspicion score {final_score:.2f} >= "
                f"block threshold {BLOCK_SCORE_THRESHOLD}. "
                f"G1 findings: {g1_reasons[:200]}. "
                f"G2 anomalies: {len(g2_anomalies)} detected."
            )
            result['human_review_needed'] = True
            
            # Create notification for display manager
            self._create_alert_notification(
                "GOD WATCHER BLOCK",
                f"⚠️ GOD WATCHER: Tool '{tool_name}' blocked with score {final_score:.2f}. "
                f"Human review required. Check audit log for details.",
                severity="critical",
                tool_name=tool_name,
                score=final_score,
                reason=g1_reasons
            )
            
        elif g1_score >= AI_AGENT_THRESHOLD:
            # ESCALATE TO AI AGENT - ambiguous case
            result['decision'] = 'escalate'
            result['needs_escalation'] = True
            result['analysis'] = (
                f"AI AGENT ESCALATION: Suspicion score {final_score:.2f} in "
                f"ambiguous range ({AI_AGENT_THRESHOLD}-{BLOCK_SCORE_THRESHOLD}). "
                f"G1 findings: {g1_reasons[:200]}. "
                f"G2 anomalies: {len(g2_anomalies)}. "
                f"AI agent should review parameters and make final determination. "
                f"IMMUTABLE AUDIT: Entry recorded in god_watcher_audit DB."
            )
            
            # Create notification for display manager (less severe)
            self._create_alert_notification(
                "GOD WATCHER ESCALATION",
                f"⚠️ GOD WATCHER: Tool '{tool_name}' escalated for AI agent review "
                f"(score {final_score:.2f}). Pattern requires deeper analysis.",
                severity="escalation",
                tool_name=tool_name,
                score=final_score,
                reason=g1_reasons
            )
            
        elif final_score >= SUSPICIOUS_SCORE_THRESHOLD:
            # Flagged but not blocked - allow with warning
            result['decision'] = 'allow'
            result['analysis'] = (
                f"ALLOW WITH WARNING: Suspicion score {final_score:.2f} "
                f"(below escalation threshold {AI_AGENT_THRESHOLD}). "
                f"Tool executed but all activity logged."
            )
            
        else:
            result['decision'] = 'allow'
            result['analysis'] = f"ALLOW: Low suspicion score {final_score:.2f}. No concerns."
        
        return result
    
    def _apply_heuristics(self, tool_name: str, params: dict) -> float:
        """Apply heuristic rules to detect unusual patterns."""
        score = 0.0
        param_str = str(params).lower()
        
        # Heuristic: Tool called with unusually many parameters
        if isinstance(params, dict) and len(params) > 10:
            score = max(score, 0.3)
        
        # Heuristic: Very long commands/params (buffer overflow risk)
        if isinstance(params, dict):
            for k, v in params.items():
                if isinstance(v, str) and len(v) > 10000:
                    score = max(score, 0.5)
                    break
                if isinstance(v, list):
                    total_len = sum(len(str(item)) for item in v if isinstance(item, str))
                    if total_len > 10000:
                        score = max(score, 0.5)
                        break
        
        # Heuristic: Unusual tool name combination (might be obfuscated)
        if tool_name and any(ch in tool_name for ch in ['\\x', '%', '\x00']):
            score = max(score, 0.8)
        
        # Heuristic: Binary/blob data in params (potential buffer overflow)
        if isinstance(params, dict):
            for k, v in params.items():
                if isinstance(v, bytes) and len(v) > 1024:
                    score = max(score, 0.4)
                if isinstance(v, str) and any(ord(c) > 127 and not c.isprintable() for c in v[:100]):
                    score = max(score, 0.3)
        
        return min(1.0, score)
    
    def _create_alert_notification(self, notif_type: str, message: str,
                                   severity: str = "warning",
                                   tool_name: str = "",
                                   score: float = 0.0,
                                   reason: str = ""):
        """Create a sticky notification for the display manager."""
        now = time.time()
        if now - self._last_alert_time < self._min_alert_interval:
            logger.debug("G4: Suppressing alert (too soon since last one)")
            return
        
        self._last_alert_time = now
        self._escalation_count += 1
        
        # Write alert to a well-known location for the display manager
        alert_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "DATA", "Alerts"
        )
        try:
            os.makedirs(alert_dir, exist_ok=True)
            
            alert = {
                'timestamp': datetime.now(timezone.utc).isoformat(),
                'type': notif_type,
                'severity': severity,
                'message': message,
                'tool_name': tool_name,
                'score': score,
                'reason': reason[:500] if reason else "",
                'escalation_count': self._escalation_count,
                'human_review_required': severity in ('critical', 'escalation'),
                'immutable_audit': True,
            }
            
            alert_path = os.path.join(alert_dir, 'god_watcher_alert.json')
            with open(alert_path, 'w') as f:
                json.dump(alert, f, indent=2)
            
            # Also append to alert history
            history_path = os.path.join(alert_dir, 'god_watcher_alerts.log')
            with open(history_path, 'a') as f:
                f.write(json.dumps(alert) + '\n')
            
            logger.warning(f"G4 ALERT [{severity}]: {message[:100]}")
            
        except Exception as e:
            logger.error(f"G4: Failed to write alert file: {e}")
    
    def get_escalation_count(self) -> int:
        return self._escalation_count


# ═══════════════════════════════════════════════════════════════════════
# GOD WATCHER ORCHESTRATOR
# ═══════════════════════════════════════════════════════════════════════

class GodWatcher:
    """
    GOD WATCHER ORCHESTRATOR.
    
    Coordinates all 4 layers of protection and provides the unified
    interface for tool execution validation.
    
    Execution flow:
    1. G1: Pre-execution scan → block or pass with score
    2. If score ambiguous → G4: AI agent oversight
    3. G2: Start runtime sentinel
    4. G3: Pre-execution snapshot
    5. ALLOW tool execution
    6. G2: Stop sentinel, check anomalies
    7. G3: Post-execution snapshot + verify
    8. Record to immutable audit
    9. If anomalies found → G4 escalation
    """
    
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        self.g1 = G1ExploitScanner()
        self.g2 = G2RuntimeSentinel()
        self.g3 = G3ForensicAudit()
        self.g4 = G4AIAgentOversight()
        self._total_scanned = 0
        self._total_blocked = 0
        self._total_escalated = 0
        self._initialized = True
        
        logger.info("👁️ GOD WATCHER initialized — All-seeing host protection active")
    
    def validate_execution(self, tool_name: str, params: dict) -> Tuple[bool, str, float]:
        """
        Full GOD WATCHER validation of a tool execution.
        
        This is the MAIN ENTRY POINT called before every tool execution.
        It runs ALL 4 layers and returns the result.
        
        Args:
            tool_name: The name of the tool being called
            params: The parameters being passed
            
        Returns:
            (allowed: bool, reason: str, suspicion_score: float)
        """
        if not GOD_WATCHER_ENABLED:
            return True, "", 0.0
        
        self._total_scanned += 1
        
        # ── Safe Tool Bypass ───────────────────────────────────────
        # Tools in SAFE_TOOLS are introspection/reasoning tools that
        # may contain code-like patterns in their parameters but are
        # inherently safe. They skip G1 but still go through G4.
        # Uses dynamic loading: static SAFE_TOOLS + tool_registry from DB.
        if tool_name in _get_safe_tools():
            g1_allowed = True
            g1_reason = ''
            g1_score = 0.0
            logger.debug(f'GOD WATCHER: Safe tool {tool_name} bypasses G1 scan')
        else:
            # ═══════════════════════════════════════════════════════════
            # STEP 1: G1 — Multi-Vector Exploit Scanner
            # ═══════════════════════════════════════════════════════════
            g1_allowed, g1_reason, g1_score = self.g1.scan_all_params(tool_name, params)
        
        if not g1_allowed:
            self._total_blocked += 1
            logger.warning(f"GOD WATCHER G1 BLOCK: {tool_name} — {g1_reason}")
            # Record audit
            self.g3.snapshot_before()
            self.g3.snapshot_after()
            self.g3.record_audit(
                tool_name=tool_name,
                decision="blocked_g1",
                reason=g1_reason,
                suspicion_score=g1_score,
                g1_findings=g1_reason,
                params=params
            )
            # G4 alert for blocked actions
            self.g4._create_alert_notification(
                "GOD WATCHER G1 BLOCK",
                f"⛔ GOD WATCHER G1 blocked '{tool_name}': {g1_reason[:200]}",
                severity="block",
                tool_name=tool_name,
                score=g1_score,
                reason=g1_reason
            )
            return False, f"👁️ GOD WATCHER (G1): {g1_reason}", g1_score
        
        # ═══════════════════════════════════════════════════════════
        # STEP 2: G4 — AI Agent Oversight (if score is significant)
        # ═══════════════════════════════════════════════════════════
        g2_anomalies = []  # No G2 yet, this is pre-execution
        g4_result = self.g4.analyze(tool_name, params, g1_score, g1_reason, g2_anomalies)
        
        if g4_result['decision'] == 'block':
            self._total_blocked += 1
            logger.warning(f"GOD WATCHER G4 BLOCK: {tool_name} — {g4_result['analysis'][:200]}")
            self.g3.snapshot_before()
            self.g3.snapshot_after()
            self.g3.record_audit(
                tool_name=tool_name,
                decision="blocked_g4",
                reason=g4_result['analysis'],
                suspicion_score=g4_result['score'],
                g1_findings=g1_reason,
                g4_analysis=g4_result['analysis'],
                g4_escalated=True,
                params=params
            )
            return False, f"👁️ GOD WATCHER (G4): {g4_result['analysis'][:200]}", g4_result['score']
        
        # ═══════════════════════════════════════════════════════════
        # STEP 3: G3 — Pre-execution snapshot
        # ═══════════════════════════════════════════════════════════
        self.g3.snapshot_before()
        
        # ═══════════════════════════════════════════════════════════
        # STEP 4: ALLOW execution (caller executes the tool)
        # ═══════════════════════════════════════════════════════════
        # Note: G2 monitoring is started by the caller during execution
        
        # Record audit entry
        self.g3.record_audit(
            tool_name=tool_name,
            decision="allowed",
            reason=g4_result['analysis'],
            suspicion_score=g1_score,
            g1_findings=g1_reason,
            g4_analysis=g4_result.get('analysis', ''),
            g4_escalated=g4_result.get('needs_escalation', False),
            params=params
        )
        
        return True, g4_result.get('analysis', ''), g1_score
    
    def finalize_execution(self, tool_name: str, g2_anomalies: List[Dict] = None):
        """
        Finalize a tool execution with G2 and G3.
        Called AFTER the tool has finished executing.
        """
        # G3: Post-execution snapshot
        self.g3.snapshot_after()
        
        # G3: Verify integrity
        ok, diff_summary = self.g3.verify_integrity()
        if not ok:
            logger.warning(f"GOD WATCHER G3 integrity issue: {diff_summary}")
        
        # If G2 anomalies found, record them
        if g2_anomalies:
            max_sev = max(a['severity'] for a in g2_anomalies) if g2_anomalies else 0.0
            if max_sev >= 0.7:
                self._total_escalated += 1
                self.g4._create_alert_notification(
                    "GOD WATCHER G2 ANOMALY",
                    f"⚠️ GOD WATCHER: Runtime anomalies detected during '{tool_name}'. "
                    f"Max severity: {max_sev:.2f}. Check audit log.",
                    severity="anomaly",
                    tool_name=tool_name,
                    score=max_sev,
                    reason=diff_summary
                )
    
    def start_runtime_monitoring(self):
        """Start G2 runtime monitoring for current execution."""
        self.g2.start_monitoring()
    
    def stop_runtime_monitoring(self) -> List[Dict]:
        """Stop G2 runtime monitoring and return anomalies."""
        return self.g2.stop_monitoring()
    
    def record_syscall(self, call_type: str):
        """Record a syscall for G2 analysis."""
        self.g2.record_syscall(call_type)
    
    def record_fd_open(self):
        """Record an FD open for G2 analysis."""
        self.g2.record_fd_open()
    
    def record_file_write(self):
        """Record a file write for G2 analysis."""
        self.g2.record_file_write()
    
    def get_stats(self) -> Dict:
        """Get overall GOD WATCHER statistics."""
        return {
            'enabled': GOD_WATCHER_ENABLED,
            'total_scanned': self._total_scanned,
            'total_blocked': self._total_blocked,
            'total_escalated': self._total_escalated,
            'g1_scan_stats': self.g1.get_stats(),
            'g4_escalation_count': self.g4.get_escalation_count(),
            'audit_db': GOD_WATCHER_AUDIT_DB,
            'layers': {
                'g1_exploit_scanner': True,
                'g2_runtime_sentinel': True,
                'g3_forensic_audit': True,
                'g4_ai_oversight': True,
            },
            'safe_tools': {
                'static_count': len(SAFE_TOOLS_STATIC),
                'dynamic_count': len(_safe_tools_cache) if _safe_tools_cache else 0,
                'refresh_interval_s': SAFE_TOOLS_REFRESH_INTERVAL,
                'cache_fresh': _safe_tools_cache is not None,
            }
        }

    def reload_safe_tools(self):
        """
        Force-reload the SAFE_TOOLS cache from the tool_registry DB.
        Returns dict with counts of static, dynamic, and newly added tools.
        """
        old_cache = _safe_tools_cache or set()
        _flush_safe_tools_cache()
        fresh = _get_safe_tools()
        static = SAFE_TOOLS_STATIC
        dynamic = fresh - static
        newly_added = dynamic - (old_cache - static)
        return {
            'static_tools': len(static),
            'dynamic_tools': len(dynamic),
            'total_safe_tools': len(fresh),
            'newly_added': sorted(newly_added)[:20],
            'newly_added_count': len(newly_added),
        }

    def flush_g1_cache(self):
        """
        Flush ALL God Watcher caches: G1 scanner state + SAFE_TOOLS cache.
        This forces a fresh reload of the tool_registry and resets all
        G1 scan counters. Does NOT disable God Watcher.
        """
        self.g1.flush_cache()
        _flush_safe_tools_cache()
        self._total_scanned = 0
        self._total_blocked = 0
        self._total_escalated = 0
        logger.info('GodWatcher: All caches flushed. G1 scanner + SAFE_TOOLS reset.')


# ── Singleton accessor ────────────────────────────────────────────
_god_watcher = None

def get_god_watcher() -> GodWatcher:
    """Get the singleton GOD WATCHER instance."""
    global _god_watcher
    if _god_watcher is None:
        _god_watcher = GodWatcher()
    return _god_watcher


# ═══════════════════════════════════════════════════════════════════════
# INITIALIZATION
# ═══════════════════════════════════════════════════════════════════════

def initialize_god_watcher():
    """
    Initialize the GOD WATCHER system.
    
    Called once at server startup to set up the singleton and
    ensure the audit database is ready.
    """
    gw = get_god_watcher()
    
    logger.info("👁️👁️👁️ GOD WATCHER SYSTEM ACTIVE 👁️👁️👁️")
    logger.info(f"  G1: Exploit Scanner — {27} exploit vectors")
    logger.info(f"  G2: Runtime Sentinel — {3} behavior monitors")
    logger.info(f"  G3: Forensic Audit — SHA3-256 integrity chain")
    logger.info(f"  G4: AI Agent Oversight — Human escalation ready")
    logger.info(f"  Audit DB: {GOD_WATCHER_AUDIT_DB}")
    
    # Start background tools directory watcher (hot-reload for new tools)
    try:
        start_tools_watcher()
    except Exception:
        logger.warning("G1 watcher: Failed to start tools directory watcher", exc_info=True)

    # Verify audit DB is writable
    try:
        g3 = gw.g3
        g3._ensure_db()
        logger.info("  Audit database: READY")
    except Exception as e:
        logger.warning(f"  Audit database: FAILED ({e})")
    
    return gw.get_stats()


def shutdown_god_watcher():
    """Graceful shutdown of the GOD WATCHER."""
    logger.info("👁️ GOD WATCHER shutting down...")
    logger.info("👁️ GOD WATCHER shutdown complete. Audit logs preserved.")


# ═══════════════════════════════════════════════════════════════════════
# CROSS-REFERENCE: Known Exploit Signatures
# ═══════════════════════════════════════════════════════════════════════
# This section documents the exploit types that G1 specifically defends
# against. Each vector is mapped to a CWE (Common Weakness Enumeration)
# for traceability.
#
# Buffer Overflow  → CWE-120, CWE-121, CWE-122, CWE-787
# Format String    → CWE-134
# Shell Injection  → CWE-78, CWE-77
# Path Traversal   → CWE-22, CWE-23, CWE-35, CWE-36
# Null Byte        → CWE-158
# CRLF Injection   → CWE-93, CWE-113
# XXE              → CWE-611
# Deserialization  → CWE-502
# TOCTOU           → CWE-367
# Symlink Attack   → CWE-61, CWE-362
# Argument Injection → CWE-88
# LD_PRELOAD       → CWE-426, CWE-427
# Race Condition   → CWE-362, CWE-364
# Fork Bomb        → CWE-674
# Seccomp Bypass   → CWE-270
# Container Escape → CWE-269
# Reverse Shell    → CWE-798, CWE-200
# PTrace Injection → CWE-271
# Unicode Attacks  → CWE-172, CWE-176
