#!/usr/bin/env python3
"""
YuniScripts Engine – Startup & Dependency Checker.

This script verifies that all required Python modules are available
before launching the main engine. If any dependencies are missing,
it attempts to install them locally into a side-by-side _deps directory
using pip's --target flag, so the engine can run without polluting the
system Python installation.

On first run (fresh install), start.py automatically detects the missing
configuration and launches an interactive setup wizard that guides you
through configuring the engine, RCON, AI economy, and optional services.

Usage:
    python3 start.py                   # Check deps, run first-run wizard if
                                       # needed, then start engine
    python3 start.py --check-only      # Only check deps, don't start
    python3 start.py --install-only    # Only install deps, don't start
    python3 start.py --reset-venvs     # Wipe all venv folders then start
    python3 start.py --help            # Show this help

Notes:
    You can also set "reset_venvs": true in engine_config.json to trigger
    a one-time venv wipe on the next launch (the flag is reset to false
    after wiping).
"""

import subprocess
import sys
import os
import shutil
import json
import importlib
import importlib.util
from pathlib import Path

# ---- Configuration ----
ENGINE_DIR = Path(__file__).resolve().parent
DEPS_DIR = ENGINE_DIR / "_deps"
ENGINE_MAIN = ENGINE_DIR / "main.py"
FIRST_RUN_SETUP = ENGINE_DIR / "SCRIPTS" / "TOOLS" / "first-run-setup" / "main.py"

# Config now lives in DATA/engine_config.json (centralized)
# Migrate from old location on first access
_old_config = ENGINE_DIR / "engine_config.json"
from engine.config_loader import get_config_path, save_config, load_config, is_fresh_install, mark_setup_complete
ENGINE_CONFIG = get_config_path("engine")
if _old_config.exists() and not ENGINE_CONFIG.exists():
    import shutil
    shutil.copy2(str(_old_config), str(ENGINE_CONFIG))
    print(f"  [migrate] engine_config.json -> {ENGINE_CONFIG}")

# Dependencies needed by the engine core (engine/*.py)
ENGINE_CORE_DEPS = [
    "watchdog",  # file watcher (used by engine/watcher.py)
]

# Dependencies needed by managed scripts (SCRIPTS/*/)
# These are installed per-script via their own venv, but we check
# for the core package manager tools.
SCRIPT_DEPS = [
    # Scripts install their own deps via requirements.txt + venv_manager.py
    # No additional core deps needed here – pip and venv are stdlib.
]

# Fallback no-dependency modules included in the project as pure-Python
# alternatives (these do not need pip installs):
#   FUNCTIONS/base64_fallback.py  – pure-Python base64
#   FUNCTIONS/crypto_fallback.py  – pure-Python AES-256-GCM
BUILTIN_FALLBACKS = {
    "Crypto": "FUNCTIONS/crypto_fallback.py (pure-Python AES-GCM fallback)",
    "base64": "stdlib or FUNCTIONS/base64_fallback.py (pure-Python fallback)",
}


def color(text, code):
    """Wrap text in an ANSI color code if stdout supports it."""
    if sys.stdout.isatty():
        return f"\033[{code}m{text}\033[0m"
    return text


def green(text):
    return color(text, "92")


def yellow(text):
    return color(text, "93")


def red(text):
    return color(text, "91")


def bold(text):
    return color(text, "1")


def check_python_version():
    """Verify we're running Python 3.10+."""
    print(f"  [check] Python: {sys.version}")
    if sys.version_info < (3, 10):
        print(red("  [FAIL] YuniScripts requires Python 3.10 or later."))
        print(yellow("  [HINT] Install a newer Python from python.org or your package manager."))
        return False
    print(green("  [OK]   Python version OK"))
    return True


def check_module_available(name: str) -> bool:
    """Return True if a module is importable."""
    try:
        importlib.import_module(name)
        return True
    except ImportError:
        return False


def try_import_with_deps_path(name: str) -> bool:
    """Try importing from _deps first, then system."""
    if DEPS_DIR.exists():
        sys.path.insert(0, str(DEPS_DIR))
    try:
        importlib.import_module(name)
        return True
    except ImportError:
        return False
    finally:
        if DEPS_DIR.exists() and str(DEPS_DIR) in sys.path:
            sys.path.remove(str(DEPS_DIR))


def install_module(name: str) -> bool:
    """Install a module into _deps/ using pip --target."""
    DEPS_DIR.mkdir(parents=True, exist_ok=True)
    print(f"  [install] Installing '{name}' into {DEPS_DIR}...")
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install",
             name, "--target", str(DEPS_DIR),
             "--quiet"],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode == 0:
            print(green(f"  [OK]     '{name}' installed successfully"))
            return True
        else:
            print(red(f"  [FAIL]   pip install '{name}' failed:"))
            for line in result.stderr.strip().split("\n"):
                if line:
                    print(f"           {line}")
            return False
    except subprocess.TimeoutExpired:
        print(red(f"  [FAIL]   pip install '{name}' timed out"))
        return False
    except FileNotFoundError:
        print(red("  [FAIL]   pip not found. Is pip installed?"))
        print(yellow("  [HINT]   Run: python3 -m ensurepip --upgrade"))
        return False


def check_engine_deps() -> bool:
    """Check and optionally install engine core dependencies."""
    print(bold("\n--- Engine Core Dependencies ---"))
    all_ok = True

    for dep in ENGINE_CORE_DEPS:
        print(f"  [check] {dep}...", end=" ")
        if try_import_with_deps_path(dep):
            print(green("found"))
        else:
            print(red("MISSING"))
            if not args.install_only and not args.check_only:
                # During normal startup, we try to install
                if install_module(dep):
                    # Add deps path to sys for subsequent checks
                    if str(DEPS_DIR) not in sys.path:
                        sys.path.insert(0, str(DEPS_DIR))
                else:
                    all_ok = False
            else:
                all_ok = False

    return all_ok


def check_main_entry() -> bool:
    """Verify that the main engine file exists."""
    if not ENGINE_MAIN.exists():
        print(red(f"  [FAIL] Engine main file not found: {ENGINE_MAIN}"))
        print(yellow("  [HINT] Ensure the directory structure is intact."))
        return False
    print(green(f"  [OK]   Engine entry: {ENGINE_MAIN}"))
    return True


def check_scripts_structure() -> bool:
    try:
        """Verify that SCRIPTS directories exist and are valid."""
        scripts_root = ENGINE_DIR / "SCRIPTS"
        if not scripts_root.exists():
            print(red("  [FAIL] SCRIPTS/ directory not found"))
            return False
    
        categories = [d for d in scripts_root.iterdir() if d.is_dir()]
        if not categories:
            print(yellow("  [WARN]  No script categories found in SCRIPTS/"))
            return True
    
        print(green(f"  [OK]   SCRIPTS/ found with {len(categories)} categories:"))
        for cat in sorted(categories):
            scripts = [d.name for d in cat.iterdir() if d.is_dir() and (d / "main.py").exists()]
            if scripts:
                print(f"         {cat.name}/: {', '.join(scripts)}")
            else:
                print(f"         {cat.name}/: (no scripts)")
    
        return True
    except Exception as e:
        logger.error(f"check_scripts_struct failed: {e}")
        return False


def read_engine_config() -> dict:
    """Read engine_config from centralized DATA/ dir."""
    return load_config("engine", {})


def write_engine_config(data: dict):
    """Write engine_config to centralized DATA/ dir."""
    save_config("engine", data)


def wipe_all_venvs() -> bool:
    """Walk SCRIPTS/ and delete every venv/ directory.

    Returns True if at least one venv was removed, False otherwise.
    """
    scripts_root = ENGINE_DIR / "SCRIPTS"
    if not scripts_root.exists():
        print(yellow("  [wipe]  SCRIPTS/ not found, nothing to wipe."))
        return False

    found = list(scripts_root.rglob("venv"))
    # Filter to only directories named "venv" (not files)
    venv_dirs = [d for d in found if d.is_dir()]

    if not venv_dirs:
        print("  [wipe]  No venv folders found.")
        return False

    count = 0
    for vd in venv_dirs:
        print(f"  [wipe]  Removing {vd}...", end=" ")
        try:
            shutil.rmtree(vd)
            print(green("done"))
            count += 1
        except OSError as e:
            print(red(f"ERROR: {e}"))

    print(green(f"  [wipe]  Removed {count} venv director{'y' if count == 1 else 'ies'}."))
    return count > 0


def check_pip_available() -> bool:
    """Ensure pip is available for dependency installation."""
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "--version"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            print(green(f"  [OK]   pip: {result.stdout.strip()}"))
            return True
        else:
            print(red("  [FAIL] pip not functional"))
            return False
    except FileNotFoundError:
        print(red("  [FAIL] pip not found"))
        return False


def ensure_deps_in_sys_path():
    """If _deps/ exists, add it to sys.path for the engine."""
    if DEPS_DIR.exists() and str(DEPS_DIR) not in sys.path:
        sys.path.insert(0, str(DEPS_DIR))
        print(f"  [info] Added {DEPS_DIR} to sys.path")


def show_summary(success: bool):
    """Print a final banner."""
    if success:
        print(bold(green("\n=== All checks passed! ===")))
    else:
        print(bold(red("\n=== Some checks FAILED ===")))
        print(yellow("Review the messages above and install missing dependencies."))
        print(yellow("You can re-run with --install-only to retry installation.\n"))


def parse_args():
    try:
        """Simple argument parsing without argparse (no deps needed).
    
        Supports combined short flags: -cr = --check-only + --reset-venvs
        -ci = --check-only + --install-only
        """
        class Args:
            check_only = False
            install_only = False
            reset_venvs = False
            help = False
    
        args = Args()
        for arg in sys.argv[1:]:
            if arg in ("--check-only", "-c"):
                args.check_only = True
            elif arg in ("--install-only", "-i"):
                args.install_only = True
            elif arg in ("--reset-venvs", "-r"):
                args.reset_venvs = True
            elif arg in ("--help", "-h"):
                args.help = True
            elif arg.startswith("-") and not arg.startswith("--"):
                # Combined flags: each character is a flag
                for flag in arg[1:]:
                    if flag == "c":
                        args.check_only = True
                    elif flag == "i":
                        args.install_only = True
                    elif flag == "r":
                        args.reset_venvs = True
                    elif flag == "h":
                        args.help = True
                    else:
                        print(f"Unknown flag: -{flag}")
                        args.help = True
            else:
                print(f"Unknown argument: {arg}")
                args.help = True
        return args
    except Exception as e:
        logger.error(f"parse_args failed: {e}")
        return None


def print_help():
    print(__doc__)


def main():
    global args
    args = parse_args()

    if args.help:
        print_help()
        sys.exit(0)

    print(bold("YuniScripts Engine – Startup Checker"))
    print("=" * 45)

    # Phase 1: Environment checks
    py_ok = check_python_version()
    pip_ok = check_pip_available()
    main_ok = check_main_entry()

    if not py_ok or not main_ok:
        show_summary(False)
        sys.exit(1)

    # Phase 2: Dependency checks
    deps_ok = check_engine_deps()
    ensure_deps_in_sys_path()

    # Phase 3: Script structure check
    scripts_ok = check_scripts_structure()

    overall = deps_ok and scripts_ok
    show_summary(overall)

    if args.check_only or args.install_only:
        sys.exit(0 if overall else 1)

    # Phase 4: Venv wipe (cross-platform migration helper)
    do_wipe = args.reset_venvs
    if not do_wipe:
        cfg = read_engine_config()
        do_wipe = cfg.get("reset_venvs", False)
    if do_wipe:
        print(bold("\n--- Virtual Environment Reset ---"))
        print(yellow("  [wipe]  --reset-venvs requested, deleting all venv folders..."))
        wiped = wipe_all_venvs()
        if wiped:
            # Reset the config flag so it only happens once
            cfg = read_engine_config()
            cfg["reset_venvs"] = False
            write_engine_config(cfg)
            print(green("  [wipe]  Config flag reset to false. Venvs will be recreated on next step."))

    # Phase 5: First-run setup (interactive config wizard)
    if is_fresh_install():
        print(bold("\n--- First-Run Configuration ---"))
        print(yellow("  [setup]  Fresh install detected. Launching interactive config wizard..."))
        if FIRST_RUN_SETUP.exists():
            try:
                result = subprocess.run(
                    [sys.executable, str(FIRST_RUN_SETUP)],
                    cwd=str(ENGINE_DIR),
                )
                if result.returncode == 0:
                    print(green("  [setup]  Configuration complete."))
                else:
                    print(yellow("  [setup]  Wizard exited early. You can re-run it later with:"))
                    print(yellow(f"           python3 {FIRST_RUN_SETUP.relative_to(ENGINE_DIR)}"))
            except Exception as e:
                print(yellow(f"  [setup]  Could not launch wizard: {e}"))
                print(yellow(f"           Run manually: python3 {FIRST_RUN_SETUP.relative_to(ENGINE_DIR)}"))
        else:
            print(yellow("  [setup]  First-run setup script not found — skipping."))
    else:
        print(bold("\n--- Configuration Check ---"))
        print(green("  [setup]  Already configured. Skipping first-run wizard."))

    # Phase 6: Launch the engine
    if not overall:
        print(red("Dependencies are missing. Run with --install-only to retry."))
        sys.exit(1)

    print(bold("\n--- Starting YuniScripts Engine ---"))
    print(f"Launching: {ENGINE_MAIN}\n")

    # Build PYTHONPATH to include _deps if present
    env = os.environ.copy()
    pythonpath_parts = []
    if DEPS_DIR.exists():
        pythonpath_parts.append(str(DEPS_DIR))
    existing = env.get("PYTHONPATH", "")
    if existing:
        pythonpath_parts.append(existing)
    if pythonpath_parts:
        env["PYTHONPATH"] = os.pathsep.join(pythonpath_parts)

    try:
        # Launch engine as a child process. We use Popen instead of
        # subprocess.run(capture_output=True) because the engine runs
        # indefinitely — captured output would never be visible to the
        # user until the engine exits (which it doesn't, normally).
        # Inheriting stdout/stderr lets the user see engine output live.
        proc = subprocess.Popen(
            [sys.executable, str(ENGINE_MAIN)],
            env=env,
            cwd=str(ENGINE_DIR),
        )

        # Wait until the engine exits (Ctrl+C or crash).
        proc.wait()

        if proc.returncode != 0:
            print(red(f"\nEngine exited with error code {proc.returncode}"))
            sys.exit(proc.returncode)

    except KeyboardInterrupt:
        print("\nShutdown requested.")
        # The engine child process also receives SIGINT on most terminals,
        # but on Windows we should explicitly terminate it.
        if 'proc' in locals() and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
    except Exception as e:
        print(red(f"Failed to launch engine: {e}"))
        sys.exit(1)


if __name__ == "__main__":
    main()
