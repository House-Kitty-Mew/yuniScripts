"""Multi-Server Manager - Central management for multiple game servers."""
__version__ = "1.0.0"

# Ensure the package directory is on sys.path so sibling imports (engine.*) work
# regardless of how this package is loaded (direct import, test discovery, etc.)
import sys
import os
_pkg_dir = os.path.dirname(os.path.abspath(__file__))
if _pkg_dir not in sys.path:
    sys.path.insert(0, _pkg_dir)
