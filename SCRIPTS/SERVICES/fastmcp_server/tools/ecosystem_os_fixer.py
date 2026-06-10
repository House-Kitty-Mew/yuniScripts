"""ecosystem_os_fixer.py — Ports OS-specific code to use ecosystem_os_abstraction.py

Scans .py files for raw OS calls and generates fix patches.
Part of WO #208 (Ecosystem Parity) and WO #253 (Phase C YS Migration).

Usage:
    python3 ecosystem_os_fixer.py /path/to/file.py
    python3 ecosystem_os_fixer.py --scan /path/to/project/
"""

import ast
import os
import re
import sys
from pathlib import Path

# OS-specific patterns to detect
RAW_OS_PATTERNS = {
    "/proc/": {
        "type": "PROC_FS",
        "message": "Replace with ecosystem_os_abstraction.system_*() or process_*()",
        "replacement": "system_loadavg() / process_info() / system_memory()"
    },
    "os.kill": {
        "type": "OS_KILL",
        "message": "Replace with ecosystem_os_abstraction.process_kill()",
        "replacement": "process_kill(pid, signal)"
    },
    "os.killpg": {
        "type": "OS_KILLPG",
        "message": "Replace with ecosystem_os_abstraction.process_kill_group()",
        "replacement": "process_kill_group(pgid, signal)"
    },
    "signal.signal": {
        "type": "SIGNAL",
        "message": "Replace with ecosystem_os_abstraction.set_signal_handler()",
        "replacement": "set_signal_handler(signum, handler)"
    },
    "signal.SIGTERM": {
        "type": "SIGNAL_CONST",
        "message": "Replace with ecosystem_os_abstraction.Signals.SIGTERM",
        "replacement": "Signals.SIGTERM"
    },
    "signal.SIGKILL": {
        "type": "SIGNAL_CONST",
        "message": "Replace with ecosystem_os_abstraction.Signals.SIGKILL",
        "replacement": "Signals.SIGKILL"
    },
    "os.chmod": {
        "type": "CHMOD",
        "message": "Replace with ecosystem_os_abstraction.set_permissions()",
        "replacement": "set_permissions(path, mode)"
    },
    "os.access": {
        "type": "ACCESS",
        "message": "Consider ecosystem_os_abstraction.is_executable()",
        "replacement": "is_executable(path)"
    },
    # Linux-specific subprocess commands
    "'ps'": {
        "type": "SUBPS",
        "message": "Replace with ecosystem_os_abstraction.process_list()",
        "replacement": "process_list()"
    },
    "'kill'": {
        "type": "SUBKILL",
        "message": "Replace with ecosystem_os_abstraction.process_kill()",
        "replacement": "process_kill(pid, signal)"
    },
    "'grep'": {
        "type": "GREP",
        "message": "Consider cross-platform subprocess via ecosystem_os_abstraction.run_command()",
        "replacement": "run_command(['findstr', ...]) on Windows"
    },
    # Hardcoded paths
    "'/home/deck'": {
        "type": "HARDCODED_PATH",
        "message": "Replace with ecosystem_os_abstraction.home_dir()",
        "replacement": "home_dir()"
    },
    # Windows-incompatible commands
    "'which'": {
        "type": "WHICH",
        "message": "Replace with ecosystem_os_abstraction.find_command()",
        "replacement": "find_command(cmd)"
    },
}


def scan_file(filepath: str) -> dict:
    """Scan a single .py file for OS-specific patterns."""
    results = {
        "file": filepath,
        "issues": [],
        "line_count": 0,
    }
    try:
        with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
            lines = f.readlines()
        results["line_count"] = len(lines)
    except Exception as e:
        results["error"] = str(e)
        return results

    for i, line in enumerate(lines, 1):
        for pattern, info in RAW_OS_PATTERNS.items():
            if pattern in line:
                # Skip if already using the abstraction layer
                if "ecosystem_os_abstraction" in line:
                    continue
                results["issues"].append({
                    "line": i,
                    "pattern": pattern,
                    "type": info["type"],
                    "code": line.strip()[:100],
                    "fix": info["message"],
                    "replacement": info["replacement"],
                })

    return results


def scan_directory(root: str) -> list:
    """Recursively scan all .py files in a directory."""
    try:
        results = []

    except Exception as e:
        logger.error(f"scan_directory failed: {e}")
        return []
    root_path = Path(root)
    if not root_path.exists():
        print(f"❌ Path not found: {root}")
        return results

    py_files = list(root_path.rglob("*.py"))
    print(f"Scanning {len(py_files)} .py files in {root}...")

    for py_file in py_files:
        if "__pycache__" in str(py_file) or ".venv" in str(py_file) or "/trash/" in str(py_file):
            continue
        result = scan_file(str(py_file))
        if result["issues"]:
            results.append(result)

    return results


def generate_report(results: list) -> str:
    """Generate a human-readable report from scan results."""
    lines = []
    lines.append("=" * 70)
    lines.append("CROSS-OS FIX REPORT — ecosystem_os_abstraction migration needed")
    lines.append("=" * 70)
    lines.append("")

    total_issues = sum(len(r["issues"]) for r in results)
    lines.append(f"Files with issues: {len(results)}")
    lines.append(f"Total OS-specific calls: {total_issues}")
    lines.append("")

    # Group by issue type
    by_type = {}
    for r in results:
        for issue in r["issues"]:
            t = issue["type"]
            if t not in by_type:
                by_type[t] = 0
            by_type[t] += 1

    lines.append("By category:")
    for t, count in sorted(by_type.items(), key=lambda x: -x[1]):
        lines.append(f"  {t:<15}: {count}")

    lines.append("")
    lines.append("-" * 70)
    lines.append("")

    # Per-file details
    for r in results:
        rel_path = os.path.relpath(r["file"], start=os.getcwd())
        lines.append(f"\n📄 {rel_path} ({r['line_count']} lines, {len(r['issues'])} issues)")
        for issue in r["issues"][:10]:  # Max 10 per file
            lines.append(f"  L{issue['line']:>4} [{issue['type']:<12}] {issue['code'][:70]}")
            lines.append(f"       → {issue['fix']}")
        if len(r["issues"]) > 10:
            lines.append(f"  ... and {len(r['issues'])-10} more issues")

    lines.append("")
    lines.append("=" * 70)
    return "\n".join(lines)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 ecosystem_os_fixer.py <file.py | --scan <dir>>")
        sys.exit(1)

    if sys.argv[1] == "--scan" and len(sys.argv) >= 3:
        results = scan_directory(sys.argv[2])
        report = generate_report(results)
        print(report)
    else:
        result = scan_file(sys.argv[1])
        if result["issues"]:
            print(f"\n📄 {result['file']} ({result['line_count']} lines)")
            for issue in result["issues"]:
                print(f"  L{issue['line']:>4} [{issue['type']:<12}] {issue['code'][:80]}")
                print(f"       → {issue['fix']}")
        else:
            print(f"✅ {result['file']} — no OS-specific issues found")

