# AH Extensions Package
# Extension directories placed under EXTENSIONS/ are auto-discovered
# by ah_plugin_registry.py at startup.
# 
# Each extension must have an __init__.py with an on_load(registry) function
# that registers its hooks.

# ── Extension metadata (populated by the registry) ──────────────────
_loaded_extensions: list[str] = []


def get_loaded() -> list[str]:
    """Return names of all currently loaded extensions."""
    return list(_loaded_extensions)
