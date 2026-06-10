"""
ecosystem_config.py - Environment-based database path configuration.

Provides ecosystem-aware database path resolution for AIHandler and
YuniScripts ecosystems. Paths are configured via environment variables
with sensible fallback defaults.

Environment variables:
  DEEPSKY_ECOSYSTEM          = "aihandler" | "yuniscripts"
  DEEPSKY_DOCS_DB_PATH       = absolute path to Documentation.db
  DEEPSKY_FILE_INDEX_DB      = absolute path to file_index_cache.db
  DEEPSKY_BACKUP_DB          = absolute path to tool_backups.db
  DEEPSKY_YUNISCRIPTS_BASE   = absolute path to YuniScripts base dir
  DEEPSKY_FASTMCP_TOOLS_PATH = absolute path to FastMCPServer tools dir
  DEEPSKY_MCP_SERVER_PATH    = absolute path to FastMCPServer server dir
  DEEPSKY_AIHANDLER_PATH     = absolute path to AIHandler root

Usage:
    from ecosystem_config import get_documentation_db_path
    db_path = get_documentation_db_path()
"""

import os
from pathlib import Path


# ---- Ecosystem Detection ---------------------------------------------------

def get_ecosystem() -> str:
    """Return ecosystem identifier: 'aihandler' (default) or 'yuniscripts'."""
    val = os.environ.get('DEEPSKY_ECOSYSTEM', '').strip().lower()
    if val in ('yuniscripts', 'ys', 'yun', 'y'):
        return 'yuniscripts'
    return 'aihandler'


def is_aihandler() -> bool:
    return get_ecosystem() == 'aihandler'


def is_yuniscripts() -> bool:
    return get_ecosystem() == 'yuniscripts'


# ---- Base Paths ------------------------------------------------------------

def get_yuniscripts_base_path() -> str:
    """Return base path for YuniScripts ecosystem.

    Default: ~/Documents/dev-yuniScripts
    Override with DEEPSKY_YUNISCRIPTS_BASE env var.
    """
    env_path = os.environ.get('DEEPSKY_YUNISCRIPTS_BASE')
    if env_path:
        return env_path
    return str(Path.home() / "Documents" / "dev-yuniScripts")


def get_aihandler_path() -> str:
    """Return root path for AIHandler ecosystem.

    Default: ~/AIHandler
    Override with DEEPSKY_AIHANDLER_PATH env var.
    """
    env_path = os.environ.get('DEEPSKY_AIHANDLER_PATH')
    if env_path:
        return env_path
    return str(Path.home() / "AIHandler")


def get_fastmcp_tools_path() -> str:
    """Return path to FastMCPServer tools directory.

    YuniScripts default: <YUNISCRIPTS_BASE>/SCRIPTS/SERVICES/fastmcp_server/tools
    AIHandler default:   ~/AIHandler/SCRIPTS/FastMCPServer/tools
    Override with DEEPSKY_FASTMCP_TOOLS_PATH env var.
    """
    env_path = os.environ.get('DEEPSKY_FASTMCP_TOOLS_PATH')
    if env_path:
        return env_path
    if is_yuniscripts():
        return str(Path(get_yuniscripts_base_path()) / "SCRIPTS" / "SERVICES" / "fastmcp_server" / "tools")
    return str(Path(get_aihandler_path()) / "SCRIPTS" / "FastMCPServer" / "tools")


def get_mcp_server_path() -> str:
    """Return path to FastMCPServer server directory.

    YuniScripts default: <YUNISCRIPTS_BASE>/SCRIPTS/SERVICES/fastmcp_server
    AIHandler default:   ~/AIHandler/SCRIPTS/FastMCPServer
    Override with DEEPSKY_MCP_SERVER_PATH env var.
    """
    env_path = os.environ.get('DEEPSKY_MCP_SERVER_PATH')
    if env_path:
        return env_path
    if is_yuniscripts():
        return str(Path(get_yuniscripts_base_path()) / "SCRIPTS" / "SERVICES" / "fastmcp_server")
    return str(Path(get_aihandler_path()) / "SCRIPTS" / "FastMCPServer")


# ---- Database Paths --------------------------------------------------------

def get_documentation_db_path() -> str:
    """Return path to Documentation.db for this ecosystem.

    AIHandler default:
        ~/AIHandler/SCRIPTS/DatabaseHandler/DATA/Databases/Documentation.db
    YuniScripts default:
        ~/Documents/dev-yuniScripts/DATA/Databases/Documentation.db

    Override with DEEPSKY_DOCS_DB_PATH env var.
    """
    env_path = os.environ.get('DEEPSKY_DOCS_DB_PATH')
    if env_path:
        return env_path

    home = Path.home()
    if is_yuniscripts():
        return str(home / "Documents" / "dev-yuniScripts" / "DATA"
                   / "Databases" / "Documentation.db")

    return str(home / "AIHandler" / "SCRIPTS" / "DatabaseHandler"
               / "DATA" / "Databases" / "Documentation.db")


def get_file_index_cache_path() -> str:
    """Return path to file_index_cache.db per ecosystem.

    AIHandler:   ~/.local_mcp/file_index_cache_aihandler.db
    YuniScripts: ~/.local_mcp/file_index_cache_yuniscripts.db

    Override with DEEPSKY_FILE_INDEX_DB env var.
    """
    env_path = os.environ.get('DEEPSKY_FILE_INDEX_DB')
    if env_path:
        return env_path

    db_name = "file_index_cache_{}.db".format(get_ecosystem())
    return str(Path.home() / ".local_mcp" / db_name)


def get_backup_db_path():
    """Return tool_backups.db path override or None for default."""
    return os.environ.get('DEEPSKY_BACKUP_DB')


# ---- Config Info ------------------------------------------------------------

def get_ecosystem_config_info() -> dict:
    """Return current ecosystem configuration for logging."""
    return {
        'ecosystem': get_ecosystem(),
        'documentation_db': get_documentation_db_path(),
        'file_index_cache': get_file_index_cache_path(),
        'backup_db': get_backup_db_path() or '(default relative path)',
        'yuniscripts_base': get_yuniscripts_base_path(),
        'aihandler_path': get_aihandler_path(),
        'fastmcp_tools': get_fastmcp_tools_path(),
        'mcp_server': get_mcp_server_path(),
    }
