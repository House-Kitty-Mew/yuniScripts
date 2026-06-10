"""
run_pytest.py — Run SIMULATED_TRADE tests using venv's pytest installation.

Adds the gui-dashboard venv's site-packages to the Python path and
executes pytest on the tests directory.
"""

import sys, os
from pathlib import Path

# Add venv site-packages to path
VENV_SITE = Path("/home/deck/Documents/dev-yuniScripts/trash/SCRIPTS/TOOLS/gui-dashboard/venv/venv/lib/python3.13/site-packages")
if VENV_SITE.exists():
    sys.path.insert(0, str(VENV_SITE))

# Add AH directory to path
AH_DIR = Path(__file__).parent.parent.parent.parent  # AUCTIONHOUSE/
sys.path.insert(0, str(AH_DIR.parent.parent.parent))  # dev-yuniScripts/

# Also add the project root
sys.path.insert(0, str(AH_DIR))

import pytest

if __name__ == "__main__":
    tests_dir = Path(__file__).parent / "tests"
    sys.exit(pytest.main([
        str(tests_dir),
        "-v",
        "--tb=short",
        "--maxfail=5",
    ]))
