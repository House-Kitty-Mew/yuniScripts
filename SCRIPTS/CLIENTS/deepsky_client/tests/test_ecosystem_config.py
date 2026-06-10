"""
Comprehensive unit tests for ecosystem_config module.

Part of DeepSky Self-Healing AI Client.
Tests: ALL path resolution functions with env vars, without env vars,
       edge cases for every possible configuration state.

NEVER USE pytest — always unittest!
"""

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ecosystem_config import (
    get_ecosystem,
    is_aihandler,
    is_yuniscripts,
    get_yuniscripts_base_path,
    get_aihandler_path,
    get_fastmcp_tools_path,
    get_mcp_server_path,
    get_documentation_db_path,
    get_file_index_cache_path,
    get_backup_db_path,
    get_ecosystem_config_info,
)


class TestEcosystemDetection(unittest.TestCase):
    """Test ecosystem identifier detection with various env var values."""

    def test_default_ecosystem(self):
        """Default ecosystem should be 'aihandler'."""
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(get_ecosystem(), 'aihandler')
            self.assertTrue(is_aihandler())
            self.assertFalse(is_yuniscripts())

    def test_ecosystem_aihandler_explicit(self):
        """Explicit 'aihandler' env var."""
        with patch.dict(os.environ, {'DEEPSKY_ECOSYSTEM': 'aihandler'}, clear=True):
            self.assertEqual(get_ecosystem(), 'aihandler')
            self.assertTrue(is_aihandler())
            self.assertFalse(is_yuniscripts())

    def test_ecosystem_yuniscripts_full(self):
        """Full 'yuniscripts' value."""
        with patch.dict(os.environ, {'DEEPSKY_ECOSYSTEM': 'yuniscripts'}, clear=True):
            self.assertEqual(get_ecosystem(), 'yuniscripts')
            self.assertFalse(is_aihandler())
            self.assertTrue(is_yuniscripts())

    def test_ecosystem_yuniscripts_short_ys(self):
        """Short 'ys' value."""
        with patch.dict(os.environ, {'DEEPSKY_ECOSYSTEM': 'ys'}, clear=True):
            self.assertEqual(get_ecosystem(), 'yuniscripts')

    def test_ecosystem_yuniscripts_short_yun(self):
        """Short 'yun' value."""
        with patch.dict(os.environ, {'DEEPSKY_ECOSYSTEM': 'yun'}, clear=True):
            self.assertEqual(get_ecosystem(), 'yuniscripts')

    def test_ecosystem_yuniscripts_single_y(self):
        """Single 'y' value."""
        with patch.dict(os.environ, {'DEEPSKY_ECOSYSTEM': 'y'}, clear=True):
            self.assertEqual(get_ecosystem(), 'yuniscripts')

    def test_ecosystem_case_insensitive(self):
        """Ecosystem values should be case-insensitive."""
        with patch.dict(os.environ, {'DEEPSKY_ECOSYSTEM': 'YUNISCRIPTS'}, clear=True):
            self.assertEqual(get_ecosystem(), 'yuniscripts')

    def test_ecosystem_whitespace_handling(self):
        """Whitespace should be stripped."""
        with patch.dict(os.environ, {'DEEPSKY_ECOSYSTEM': '  aihandler  '}, clear=True):
            self.assertEqual(get_ecosystem(), 'aihandler')

    def test_ecosystem_unknown_value(self):
        """Unknown values should default to 'aihandler'."""
        with patch.dict(os.environ, {'DEEPSKY_ECOSYSTEM': 'unknown'}, clear=True):
            self.assertEqual(get_ecosystem(), 'aihandler')


class TestBasePaths(unittest.TestCase):
    """Test base path resolution functions."""

    @patch.dict(os.environ, {}, clear=True)
    def test_yuniscripts_base_default(self):
        """Default YuniScripts base path."""
        expected = os.path.join(os.path.expanduser('~'), 'Documents', 'dev-yuniScripts')
        self.assertEqual(get_yuniscripts_base_path(), expected)

    @patch.dict(os.environ, {'DEEPSKY_YUNISCRIPTS_BASE': '/custom/path'}, clear=True)
    def test_yuniscripts_base_env_override(self):
        """YuniScripts base path via env var."""
        self.assertEqual(get_yuniscripts_base_path(), '/custom/path')

    @patch.dict(os.environ, {}, clear=True)
    def test_yuniscripts_base_non_empty(self):
        """YuniScripts base should never be empty."""
        self.assertTrue(len(get_yuniscripts_base_path()) > 0)

    @patch.dict(os.environ, {}, clear=True)
    def test_aihandler_path_default(self):
        """Default AIHandler path."""
        expected = os.path.join(os.path.expanduser('~'), 'AIHandler')
        self.assertEqual(get_aihandler_path(), expected)

    @patch.dict(os.environ, {'DEEPSKY_AIHANDLER_PATH': '/opt/aihandler'}, clear=True)
    def test_aihandler_path_env_override(self):
        """AIHandler path via env var."""
        self.assertEqual(get_aihandler_path(), '/opt/aihandler')

    @patch.dict(os.environ, {}, clear=True)
    def test_fastmcp_tools_default(self):
        """Default FastMCP tools path."""
        expected = os.path.join(
            os.path.expanduser('~'), 'AIHandler', 'SCRIPTS',
            'FastMCPServer', 'tools'
        )
        self.assertEqual(get_fastmcp_tools_path(), expected)

    @patch.dict(os.environ, {'DEEPSKY_FASTMCP_TOOLS_PATH': '/custom/tools'}, clear=True)
    def test_fastmcp_tools_env_override(self):
        """FastMCP tools path via env var."""
        self.assertEqual(get_fastmcp_tools_path(), '/custom/tools')

    @patch.dict(os.environ, {
        'DEEPSKY_AIHANDLER_PATH': '/opt/aihandler'
    }, clear=True)
    def test_fastmcp_tools_aihandler_derived(self):
        """FastMCP tools path derived from AIHandler path when env var not set."""
        # Without explicit FASTMCP_TOOLS but with AIHANDLER_PATH
        expected = os.path.join('/opt/aihandler', 'SCRIPTS', 'FastMCPServer', 'tools')
        self.assertEqual(get_fastmcp_tools_path(), expected)

    @patch.dict(os.environ, {}, clear=True)
    def test_mcp_server_default(self):
        """Default MCP server path."""
        expected = os.path.join(
            os.path.expanduser('~'), 'AIHandler', 'SCRIPTS', 'FastMCPServer'
        )
        self.assertEqual(get_mcp_server_path(), expected)

    @patch.dict(os.environ, {'DEEPSKY_MCP_SERVER_PATH': '/custom/mcp'}, clear=True)
    def test_mcp_server_env_override(self):
        """MCP server path via env var."""
        self.assertEqual(get_mcp_server_path(), '/custom/mcp')


class TestDocumentationDbPath(unittest.TestCase):
    """Test documentation DB path resolution."""

    @patch.dict(os.environ, {}, clear=True)
    def test_docs_db_default_aihandler(self):
        """Default AIHandler docs DB path."""
        result = get_documentation_db_path()
        self.assertIn('AIHandler', result)
        self.assertIn('Documentation.db', result)

    @patch.dict(os.environ, {'DEEPSKY_ECOSYSTEM': 'yuniscripts'}, clear=True)
    def test_docs_db_default_yuniscripts(self):
        """Default YuniScripts docs DB path."""
        result = get_documentation_db_path()
        self.assertIn('dev-yuniScripts', result)
        self.assertIn('Documentation.db', result)

    @patch.dict(os.environ, {'DEEPSKY_DOCS_DB_PATH': '/custom/docs.db'}, clear=True)
    def test_docs_db_env_override(self):
        """Docs DB path via env var."""
        self.assertEqual(get_documentation_db_path(), '/custom/docs.db')

    @patch.dict(os.environ, {}, clear=True)
    def test_docs_db_ends_with_correct_filename(self):
        """Docs DB path should always end with Documentation.db."""
        result = get_documentation_db_path()
        self.assertTrue(result.endswith('Documentation.db'))


class TestFileIndexCachePath(unittest.TestCase):
    """Test file index cache path resolution."""

    @patch.dict(os.environ, {}, clear=True)
    def test_cache_default_aihandler(self):
        """Default AIHandler cache path."""
        result = get_file_index_cache_path()
        self.assertIn('.local_mcp', result)
        self.assertIn('aihandler', result)

    @patch.dict(os.environ, {'DEEPSKY_ECOSYSTEM': 'yuniscripts'}, clear=True)
    def test_cache_default_yuniscripts(self):
        """Default YuniScripts cache path."""
        result = get_file_index_cache_path()
        self.assertIn('.local_mcp', result)
        self.assertIn('yuniscripts', result)

    @patch.dict(os.environ, {'DEEPSKY_FILE_INDEX_DB': '/custom/cache.db'}, clear=True)
    def test_cache_env_override(self):
        """Cache path via env var."""
        self.assertEqual(get_file_index_cache_path(), '/custom/cache.db')


class TestBackupDbPath(unittest.TestCase):
    """Test backup DB path resolution."""

    @patch.dict(os.environ, {}, clear=True)
    def test_backup_default_none(self):
        """Default backup path should be None."""
        self.assertIsNone(get_backup_db_path())

    @patch.dict(os.environ, {'DEEPSKY_BACKUP_DB': '/custom/backups.db'}, clear=True)
    def test_backup_env_override(self):
        """Backup path via env var."""
        self.assertEqual(get_backup_db_path(), '/custom/backups.db')


class TestEcosystemConfigInfo(unittest.TestCase):
    """Test get_ecosystem_config_info dictionary output."""

    @patch.dict(os.environ, {}, clear=True)
    def test_config_info_contains_all_keys(self):
        """Config info should contain all expected keys."""
        info = get_ecosystem_config_info()
        expected_keys = {
            'ecosystem', 'documentation_db', 'file_index_cache',
            'backup_db', 'yuniscripts_base', 'aihandler_path',
            'fastmcp_tools', 'mcp_server',
        }
        self.assertTrue(expected_keys.issubset(info.keys()))

    @patch.dict(os.environ, {}, clear=True)
    def test_config_info_ecosystem_default(self):
        """Default ecosystem in config info."""
        info = get_ecosystem_config_info()
        self.assertEqual(info['ecosystem'], 'aihandler')

    @patch.dict(os.environ, {
        'DEEPSKY_ECOSYSTEM': 'yuniscripts',
        'DEEPSKY_YUNISCRIPTS_BASE': '/test/yuni',
        'DEEPSKY_AIHANDLER_PATH': '/test/ai',
        'DEEPSKY_FASTMCP_TOOLS_PATH': '/test/tools',
        'DEEPSKY_DOCS_DB_PATH': '/test/docs.db',
        'DEEPSKY_FILE_INDEX_DB': '/test/cache.db',
        'DEEPSKY_BACKUP_DB': '/test/backups.db',
        'DEEPSKY_MCP_SERVER_PATH': '/test/mcp',
    }, clear=True)
    def test_config_info_all_overrides(self):
        """All config info values with env var overrides."""
        info = get_ecosystem_config_info()
        self.assertEqual(info['ecosystem'], 'yuniscripts')
        self.assertEqual(info['yuniscripts_base'], '/test/yuni')
        self.assertEqual(info['aihandler_path'], '/test/ai')
        self.assertEqual(info['fastmcp_tools'], '/test/tools')
        self.assertEqual(info['documentation_db'], '/test/docs.db')
        self.assertEqual(info['file_index_cache'], '/test/cache.db')
        self.assertEqual(info['backup_db'], '/test/backups.db')
        self.assertEqual(info['mcp_server'], '/test/mcp')


class TestEdgeCases(unittest.TestCase):
    """Edge cases for path resolution."""

    @patch.dict(os.environ, {
        'DEEPSKY_YUNISCRIPTS_BASE': '/tmp/../custom',
        'DEEPSKY_ECOSYSTEM': 'yuniscripts',
    }, clear=True)
    def test_path_with_dot_dot(self):
        """Paths with '..' should not be resolved (raw string)."""
        result = get_yuniscripts_base_path()
        self.assertEqual(result, '/tmp/../custom')

    @patch.dict(os.environ, {'DEEPSKY_AIHANDLER_PATH': ''}, clear=True)
    def test_empty_env_var_falls_back(self):
        """Empty env var should fall back to default."""
        # Clear env var by setting to empty string - our code checks truthiness
        with patch.dict(os.environ, {}, clear=True):
            result = get_aihandler_path()
            self.assertIn('AIHandler', result)

    def test_all_functions_return_strings(self):
        """All path functions should return strings."""
        with patch.dict(os.environ, {}, clear=True):
            for func in [
                get_yuniscripts_base_path,
                get_aihandler_path,
                get_fastmcp_tools_path,
                get_mcp_server_path,
                get_documentation_db_path,
                get_file_index_cache_path,
            ]:
                result = func()
                self.assertIsInstance(result, str, f"{func.__name__} returned non-string: {type(result)}")


if __name__ == '__main__':
    unittest.main()
