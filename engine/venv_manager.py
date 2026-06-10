"""Virtualenv management – pure functional style."""
import os
import subprocess
import sys
from pathlib import Path
from typing import Dict

def _venv_dir(script_path: Path) -> Path:
    return script_path / "venv"

def _venv_python(venv_path: Path) -> Path:
    """Return the path to the Python executable inside a virtual environment.
    Platform-native only — different platforms have different venv layouts:
    Windows: Scripts/python.exe   Linux/macOS: bin/python"""
    if sys.platform == "win32":
        return venv_path / "Scripts" / "python.exe"
    return venv_path / "bin" / "python"

def _create_venv(script_instance: Dict, venv_path: Path) -> Dict:
    python = script_instance["meta"]["python_path"]
    try:
        subprocess.run(
            [python, "-m", "venv", str(venv_path)],
            check=True,
            capture_output=True,
            text=True,
        )
        print(f"  [venv] created {venv_path}")
        return {**script_instance, "venv_path": venv_path, "venv_error": None}
    except subprocess.CalledProcessError as e:
        print(f"  [venv] ERROR creating {venv_path}: {e.stderr}")
        return {**script_instance, "venv_path": venv_path, "venv_error": e.stderr}

def _install_requirements(script_instance: Dict) -> Dict:
    venv_path = script_instance.get("venv_path")
    if venv_path is None:
        return script_instance
    req_file = script_instance["path"] / script_instance["meta"]["requirements_file"]
    if not req_file.exists():
        print(f"  [venv] no requirements.txt, skipping install for {script_instance['id']}")
        return script_instance
    try:
        subprocess.run(
            [str(_venv_python(venv_path)), "-m", "pip", "install", "-r", str(req_file)],
            check=True,
            capture_output=True,
            text=True,
        )
        print(f"  [venv] installed dependencies for {script_instance['id']}")
        return {**script_instance, "requirements_installed": True}
    except subprocess.CalledProcessError as e:
        print(f"  [venv] ERROR installing requirements for {script_instance['id']}: {e.stderr}")
        return {**script_instance, "requirements_installed": False, "venv_error": e.stderr}

def ensure_venv(script_instance: Dict) -> Dict:
    venv_path = _venv_dir(script_instance["path"])
    if venv_path.exists():
        # Check if the venv has a Python binary for THIS platform.
        # A venv created on Linux (bin/python) will have no Scripts/python.exe
        # when copied to Windows. In that case, recreate it on this platform.
        python_path = _venv_python(venv_path)
        if not python_path.exists():
            print(f"  [venv] venv exists but missing {python_path.name} (wrong platform?), recreating...")
            return _create_venv(script_instance, venv_path)
        print(f"  [venv] using existing venv at {venv_path}")
        return {**script_instance, "venv_path": venv_path}
    return _create_venv(script_instance, venv_path)

def install_requirements(script_instance: Dict) -> Dict:
    if "venv_path" not in script_instance:
        print(f"  [venv] no venv for {script_instance['id']}, cannot install requirements")
        return script_instance
    return _install_requirements(script_instance)

def prepare_environment(script_instance: Dict) -> Dict:
    instance = ensure_venv(script_instance)
    instance = install_requirements(instance)
    return instance