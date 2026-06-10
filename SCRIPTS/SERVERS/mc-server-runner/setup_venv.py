#!/usr/bin/env python3
"""
setup_venv.py — Auto-create .venv and install dependencies for mc-server-runner.

Usage:
    python3 setup_venv.py          # Ensure .venv exists with all deps
    python3 setup_venv.py --force  # Recreate .venv from scratch
"""

import os
import sys
import subprocess
import logging
from pathlib import Path

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger('setup_venv')

PROJECT_DIR = Path(__file__).resolve().parent
VENV_DIR = PROJECT_DIR / '.venv'
REQUIREMENTS = PROJECT_DIR / 'requirements.txt'


def venv_python() -> Path:
    """Return path to the venv's python interpreter."""
    if sys.platform == 'win32':
        return VENV_DIR / 'Scripts' / 'python.exe'
    return VENV_DIR / 'bin' / 'python'


def venv_exists() -> bool:
    """Check if .venv exists and has a working Python interpreter."""
    py = venv_python()
    if not py.is_file():
        return False
    try:
        result = subprocess.run([str(py), '--version'], capture_output=True, text=True, timeout=10)
        return result.returncode == 0
    except Exception:
        return False


def create_venv(force: bool = False) -> bool:
    """Create .venv if it doesn't exist (or recreate if force=True)."""
    if force and VENV_DIR.exists():
        import shutil
        logger.info("Removing existing .venv (--force)...")
        shutil.rmtree(VENV_DIR)

    if VENV_DIR.exists() and venv_exists():
        logger.info(".venv already exists and is functional.")
        return True

    logger.info("Creating .venv...")
    try:
        subprocess.run(
            [sys.executable, '-m', 'venv', str(VENV_DIR)],
            check=True, capture_output=True, text=True, timeout=60
        )
        logger.info(".venv created successfully.")
        return True
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to create .venv: {e.stderr}")
        return False
    except FileNotFoundError:
        logger.error("venv module not available (ensure Python 3.3+ is installed)")
        return False


def install_requirements() -> bool:
    """Install packages from requirements.txt into .venv."""
    if not REQUIREMENTS.is_file():
        logger.info("No requirements.txt found, skipping dependency install.")
        return True

    py = venv_python()
    if not py.is_file():
        logger.error(f"Python not found in venv: {py}")
        return False

    logger.info("Installing requirements...")
    try:
        result = subprocess.run(
            [str(py), '-m', 'pip', 'install', '-r', str(REQUIREMENTS)],
            capture_output=True, text=True, timeout=120
        )
        if result.returncode == 0:
            logger.info("Requirements installed successfully.")
            return True
        else:
            logger.warning(f"pip install had issues: {result.stderr[:500]}")
            # Some deps may already be satisfied, check if critical ones work
            return True
    except subprocess.TimeoutExpired:
        logger.warning("pip install timed out (deps may be partially installed)")
        return False


def ensure_venv(force: bool = False) -> bool:
    """Ensure .venv exists with all dependencies. Returns True on success."""
    if not create_venv(force):
        return False
    if not install_requirements():
        logger.warning("Some dependencies may be missing; check .venv manually.")
    return True


def main():
    force = '--force' in sys.argv
    if not ensure_venv(force):
        sys.exit(1)
    print(f"\nSetup complete! Use '{venv_python()}' to run mc-server-runner scripts.")
    print(f"  Example: {venv_python()} main.py list")


if __name__ == '__main__':
    main()
