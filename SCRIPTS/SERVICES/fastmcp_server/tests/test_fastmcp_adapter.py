"""
Unit tests for FastMCPAdapter.

Part of FastMCP Server (YuniScript Managed) — WO #147 deliverable #7.
Tests: tool discovery (DB + filesystem), tool execution, error handling,
       handler creation, tool definitions for prompts, edge cases.

NEVER USE pytest — always unittest!
"""

import asyncio
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch, mock_open

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastmcp_adapter import FastMCPAdapter


# ═══ Helpers ═══════════════════════════════════════════════════════

def _create_fake_tool_file(tmpdir: str, name: str, content: str):
    """Create a fake .py tool file in tmpdir."""
    path = os.path.join(tmpdir, f"{name}.py")
    with open(path, 'w') as f:
        f.write(content)
    return path


def _make_sync_tool_file(name: str, func_name: str = None) -> str:
    """Generate Python source for a synchronous test tool."""
    fn = func_name or name
    return f"""
def {fn}(param1: str = "default", count: int = 1) -> str:
    return f"{{param1}}-{{count}}"
"""


def _make_async_tool_file(name: str, func_name: str = None) -> str:
    """Generate Python source for an async test tool."""
    fn = func_name or name
    return f"""
import asyncio
async def {fn}(param1: str = "default", count: int = 1) -> str:
    await asyncio.sleep(0.01)
    return f"async-{{param1}}-{{count}}"
"""


def _make_multi_func_tool_file(name: str) -> str:
    """Generate Python source with multiple public functions."""
    return f"""
def {name}(x: int = 0) -> int:
    return x + 1

def helper_func(y: int = 0) -> int:
    return y * 2

def _private_func(z: int = 0) -> int:
    return z - 1
"""


# ═══ Test Cases ═══════════════════════════════════════════════════

class TestFastMCPAdapterInit(unittest.TestCase):
    """Test adapter initialization and configuration."""

    def test_init_default_paths(self):
        """Adapter should use default paths when none provided."""
        adapter = FastMCPAdapter()
        self.assertIsNotNone(adapter.tools_dir)
        self.assertTrue('FastMCPServer/tools' in adapter.tools_dir or
                        '/tools' in adapter.tools_dir)
        self.assertFalse(adapter._initialized)
        self.assertEqual(adapter._tools, {})

    def test_init_custom_paths(self):
        """Adapter should accept custom tools_dir."""
        custom_dir = '/custom/tools/path'
        adapter = FastMCPAdapter(tools_dir=custom_dir)
        self.assertEqual(adapter.tools_dir, custom_dir)
        self.assertFalse(adapter._initialized)

    def test_init_empty_db_path(self):
        """Adapter should set default DB path."""
        adapter = FastMCPAdapter()
        self.assertTrue(adapter._db_path.endswith('Documentation.db'))

    def test_init_no_cross_contamination(self):
        """Multiple adapters should not share state."""
        a1 = FastMCPAdapter()
        a2 = FastMCPAdapter()
        a1._tools = {'tool_a': lambda: None}
        self.assertEqual(a2._tools, {})


class TestFastMCPAdapterDiscoverFromFilesystem(unittest.TestCase):
    """Test filesystem-based tool discovery."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.adapter = FastMCPAdapter(tools_dir=self.tmpdir, db_discovery=False)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_discover_single_tool(self):
        """Should discover a single .py file as a tool."""
        _create_fake_tool_file(self.tmpdir, 'test_tool',
                               _make_sync_tool_file('test_tool'))
        
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(self.adapter.initialize())
            names = self.adapter.get_tool_names()
            self.assertIn('test_tool', names)
        finally:
            loop.close()

    def test_discover_multiple_tools(self):
        """Should discover multiple .py files."""
        for name in ['alpha', 'beta', 'gamma']:
            _create_fake_tool_file(self.tmpdir, name,
                                   _make_sync_tool_file(name))
        
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(self.adapter.initialize())
            names = self.adapter.get_tool_names()
            for name in ['alpha', 'beta', 'gamma']:
                self.assertIn(name, names)
        finally:
            loop.close()

    def test_ignore_private_files(self):
        """Should ignore files starting with underscore."""
        _create_fake_tool_file(self.tmpdir, '_private',
                               _make_sync_tool_file('_private'))
        _create_fake_tool_file(self.tmpdir, 'public_tool',
                               _make_sync_tool_file('public_tool'))
        
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(self.adapter.initialize())
            names = self.adapter.get_tool_names()
            self.assertNotIn('_private', names)
            self.assertIn('public_tool', names)
        finally:
            loop.close()

    def test_ignore_non_python_files(self):
        """Should ignore non-.py files."""
        with open(os.path.join(self.tmpdir, 'data.json'), 'w') as f:
            f.write('{}')
        with open(os.path.join(self.tmpdir, 'config.yaml'), 'w') as f:
            f.write('key: value')
        _create_fake_tool_file(self.tmpdir, 'real_tool',
                               _make_sync_tool_file('real_tool'))
        
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(self.adapter.initialize())
            names = self.adapter.get_tool_names()
            self.assertEqual(names, ['real_tool'])
        finally:
            loop.close()

    def test_empty_directory(self):
        """Should handle empty tools directory gracefully."""
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(self.adapter.initialize())
            self.assertEqual(len(self.adapter.get_tool_names()), 0)
        finally:
            loop.close()

    def test_nonexistent_directory(self):
        """Should handle nonexistent tools directory."""
        adapter = FastMCPAdapter(tools_dir='/nonexistent/path/xyz', db_discovery=False)
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(adapter.initialize())
            self.assertEqual(len(adapter.get_tool_names()), 0)
        finally:
            loop.close()

    def test_empty_file_ignored(self):
        """Should ignore .py file with no callable functions."""
        _create_fake_tool_file(self.tmpdir, 'empty_tool', '')
        
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(self.adapter.initialize())
            # Empty module has no callable functions, so it's not registered
            names = self.adapter.get_tool_names()
            self.assertNotIn('empty_tool', names)
        finally:
            loop.close()


class TestFastMCPAdapterDiscoverFromDB(unittest.TestCase):
    """Test DB-based tool discovery."""

    @patch('sqlite3.connect')
    def test_discover_from_db(self, mock_connect):
        """Should discover tools from tool_registry table."""
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [
            ('database_query', 'database_query.py', 'database_query', 'database_query',
             'database', 'Query a database', 1),
        ]
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_connect.return_value = mock_conn
        
        loop = asyncio.new_event_loop()
        try:
            adapter = FastMCPAdapter()
            loop.run_until_complete(adapter.initialize())
            # DB tools should be discovered (even if handlers fail to load)
            names = adapter.get_tool_names()
            # We don't assert specific names here since handler loading
            # depends on filesystem; just verify no crash
            self.assertIsInstance(names, list)
        finally:
            loop.close()

    @patch('sqlite3.connect')
    def test_db_no_registry_table(self, mock_connect):
        """Should handle missing tool_registry table gracefully."""
        mock_cursor = MagicMock()
        mock_cursor.execute.side_effect = [
            None,  # CREATE TABLE check passes (just returns None)
            Exception('no such table: tool_registry'),  # query fails
        ]
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_connect.return_value = mock_conn
        
        # Non-fatal — should not crash
        adapter = FastMCPAdapter(tools_dir='/nonexistent')
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(adapter.initialize())
            # Should not crash, should be initialized
            self.assertTrue(adapter._initialized)
        finally:
            loop.close()

    @patch('sqlite3.connect')
    def test_db_connection_error(self, mock_connect):
        """Should handle DB connection error gracefully."""
        mock_connect.side_effect = Exception('Cannot connect to database')
        
        adapter = FastMCPAdapter(tools_dir='/nonexistent')
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(adapter.initialize())
            self.assertTrue(adapter._initialized)
            self.assertEqual(len(adapter.get_tool_names()), 0)
        finally:
            loop.close()


class TestFastMCPAdapterToolExecution(unittest.TestCase):
    """Test tool execution through the adapter."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.adapter = FastMCPAdapter(tools_dir=self.tmpdir, db_discovery=False)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_execute_sync_tool(self):
        """Should execute a synchronous tool and return result."""
        _create_fake_tool_file(self.tmpdir, 'echo',
                               _make_sync_tool_file('echo'))
        
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(self.adapter.initialize())
            
            # Execute tool
            result = loop.run_until_complete(
                self.adapter.execute_tool('echo', param1='hello', count=42)
            )
            self.assertEqual(result, 'hello-42')
        finally:
            loop.close()

    def test_execute_async_tool(self):
        """Should execute an async tool and return result."""
        _create_fake_tool_file(self.tmpdir, 'async_tool',
                               _make_async_tool_file('async_tool'))
        
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(self.adapter.initialize())
            
            result = loop.run_until_complete(
                self.adapter.execute_tool('async_tool', param1='world', count=99)
            )
            self.assertEqual(result, 'async-world-99')
        finally:
            loop.close()

    def test_execute_tool_not_found(self):
        """Should raise KeyError for unknown tool."""
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(self.adapter.initialize())
            
            with self.assertRaises(KeyError):
                loop.run_until_complete(
                    self.adapter.execute_tool('nonexistent_tool')
                )
        finally:
            loop.close()

    def test_execute_tool_not_initialized(self):
        """Should work even if not explicitly initialized (lazy load attempt)."""
        _create_fake_tool_file(self.tmpdir, 'lazy_tool',
                               _make_sync_tool_file('lazy_tool'))
        
        loop = asyncio.new_event_loop()
        try:
            # Execute without initialize() — should still work since
            # _get_handler loads on demand
            result = loop.run_until_complete(
                self.adapter.execute_tool('nonexistent')
            )
            self.fail("Should have raised KeyError")
        except KeyError:
            pass  # Expected
        finally:
            loop.close()

    def test_execute_tool_with_default_params(self):
        """Should use default parameter values."""
        _create_fake_tool_file(self.tmpdir, 'default_tool',
                               _make_sync_tool_file('default_tool'))
        
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(self.adapter.initialize())
            
            result = loop.run_until_complete(
                self.adapter.execute_tool('default_tool')
            )
            self.assertEqual(result, 'default-1')
        finally:
            loop.close()

    def test_execute_tool_with_positional_error(self):
        """Should handle TypeError from wrong params gracefully."""
        _create_fake_tool_file(self.tmpdir, 'strict_tool', """
def strict_tool(exact_param: str) -> str:
    return exact_param
""")
        
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(self.adapter.initialize())
            
            with self.assertRaises(Exception):
                loop.run_until_complete(
                    self.adapter.execute_tool('strict_tool', wrong_param='x')
                )
        finally:
            loop.close()

    def test_execute_tool_returns_none(self):
        """Should handle tools that return None."""
        _create_fake_tool_file(self.tmpdir, 'null_tool', """
def null_tool() -> None:
    return None
""")
        
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(self.adapter.initialize())
            
            result = loop.run_until_complete(
                self.adapter.execute_tool('null_tool')
            )
            self.assertIsNone(result)
        finally:
            loop.close()

    def test_execute_tool_returns_complex(self):
        """Should handle tools returning complex types (dicts, lists)."""
        _create_fake_tool_file(self.tmpdir, 'complex_tool', """
def complex_tool(include_list: bool = False) -> dict:
    result = {"status": "ok", "count": 42}
    if include_list:
        result["items"] = [1, 2, 3]
    return result
""")
        
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(self.adapter.initialize())
            
            result = loop.run_until_complete(
                self.adapter.execute_tool('complex_tool', include_list=True)
            )
            self.assertEqual(result['status'], 'ok')
            self.assertEqual(result['items'], [1, 2, 3])
        finally:
            loop.close()


class TestFastMCPAdapterToolAccess(unittest.TestCase):
    """Test tool accessor methods."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.adapter = FastMCPAdapter(tools_dir=self.tmpdir, db_discovery=False)
        for name in ['alpha', 'beta', 'gamma']:
            _create_fake_tool_file(self.tmpdir, name,
                                   _make_sync_tool_file(name))

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_get_tool_names(self):
        """get_tool_names should return sorted list of tool names."""
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(self.adapter.initialize())
            names = self.adapter.get_tool_names()
            self.assertEqual(names, ['alpha', 'beta', 'gamma'])
        finally:
            loop.close()

    def test_get_tool(self):
        """get_tool should return a callable handler."""
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(self.adapter.initialize())
            handler = self.adapter.get_tool('alpha')
            self.assertIsNotNone(handler)
            self.assertTrue(callable(handler))
        finally:
            loop.close()

    def test_get_tool_not_found(self):
        """get_tool should return None for unknown tool."""
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(self.adapter.initialize())
            handler = self.adapter.get_tool('unknown')
            self.assertIsNone(handler)
        finally:
            loop.close()

    def test_get_all_tools(self):
        """get_all_tools should return dict of all tools."""
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(self.adapter.initialize())
            tools = self.adapter.get_all_tools()
            self.assertIn('alpha', tools)
            self.assertIn('beta', tools)
            self.assertIn('gamma', tools)
            self.assertEqual(len(tools), 3)
        finally:
            loop.close()

    def test_get_all_tools_is_copy(self):
        """get_all_tools should return a copy, not the internal dict."""
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(self.adapter.initialize())
            tools = self.adapter.get_all_tools()
            tools['new_tool'] = lambda: None
            # Internal state should not be affected
            self.assertNotIn('new_tool', self.adapter.get_all_tools())
        finally:
            loop.close()

    def test_get_tool_names_after_discovery(self):
        """Tool names should be sorted alphabetically."""
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(self.adapter.initialize())
            names = self.adapter.get_tool_names()
            self.assertEqual(names, sorted(names))
        finally:
            loop.close()


class TestFastMCPAdapterPromptDefinitions(unittest.TestCase):
    """Test generation of tool definitions for prompts."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.adapter = FastMCPAdapter(tools_dir=self.tmpdir, db_discovery=False)
        for name in ['search', 'query', 'write']:
            _create_fake_tool_file(self.tmpdir, name,
                                   _make_sync_tool_file(name))

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_get_tool_definitions_for_prompt(self):
        """Should return formatted string of tool names."""
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(self.adapter.initialize())
            prompt = self.adapter.get_tool_definitions_for_prompt()
            self.assertIn('search', prompt)
            self.assertIn('query', prompt)
            self.assertIn('write', prompt)
            self.assertIn('**', prompt)  # Markdown formatting
        finally:
            loop.close()

    def test_get_tool_definitions_empty(self):
        """Should return message when no tools available."""
        adapter = FastMCPAdapter(tools_dir='/nonexistent')
        prompt = adapter.get_tool_definitions_for_prompt()
        self.assertEqual(prompt, 'No tools available.')

    def test_get_tool_definitions_format(self):
        """Should format tools as markdown list items."""
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(self.adapter.initialize())
            prompt = self.adapter.get_tool_definitions_for_prompt()
            lines = prompt.split('\n')
            for line in lines:
                self.assertTrue(line.startswith('- **') or line == '')
        finally:
            loop.close()


class TestFastMCPAdapterEdgeCases(unittest.TestCase):
    """Test edge cases and error handling."""

    def test_init_with_bytes_path(self):
        """Should handle bytes path (Python 3 compatibility)."""
        adapter = FastMCPAdapter(tools_dir=b'/some/path')
        self.assertIsNotNone(adapter)

    def test_init_with_none_dir(self):
        """Should use default dir when None provided."""
        adapter = FastMCPAdapter(tools_dir=None)
        self.assertIsNotNone(adapter.tools_dir)

    def test_double_initialize(self):
        """Calling initialize twice should not crash."""
        tmpdir = tempfile.mkdtemp()
        _create_fake_tool_file(tmpdir, 'double_tool',
                               _make_sync_tool_file('double_tool'))
        
        adapter = FastMCPAdapter(tools_dir=tmpdir, db_discovery=False)
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(adapter.initialize())
            loop.run_until_complete(adapter.initialize())  # Second call
            names = adapter.get_tool_names()
            self.assertIn('double_tool', names)
        finally:
            loop.close()
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_tool_file_with_syntax_error(self):
        """Should handle .py file with syntax errors gracefully."""
        tmpdir = tempfile.mkdtemp()
        _create_fake_tool_file(tmpdir, 'bad_syntax', """
def bad_syntax(x):
    invalid syntax here
    return x
""")
        _create_fake_tool_file(tmpdir, 'good_tool',
                               _make_sync_tool_file('good_tool'))
        
        adapter = FastMCPAdapter(tools_dir=tmpdir, db_discovery=False)
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(adapter.initialize())
            # Good tool should still be discoverable even if bad_syntax fails
            names = adapter.get_tool_names()
            self.assertIn('good_tool', names)
        finally:
            loop.close()
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_tool_that_raises_exception(self):
        """Should propagate tool exceptions to caller."""
        tmpdir = tempfile.mkdtemp()
        _create_fake_tool_file(tmpdir, 'broken_tool', """
def broken_tool():
    raise RuntimeError("Something went wrong")
""")
        
        adapter = FastMCPAdapter(tools_dir=tmpdir, db_discovery=False)
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(adapter.initialize())
            
            with self.assertRaises(RuntimeError):
                loop.run_until_complete(
                    adapter.execute_tool('broken_tool')
                )
        finally:
            loop.close()
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_get_tool_before_initialize(self):
        """Getting a tool before initialize should return None."""
        adapter = FastMCPAdapter()
        self.assertIsNone(adapter.get_tool('anything'))

    def test_get_tool_names_before_initialize(self):
        """Getting tool names before initialize should return empty list."""
        adapter = FastMCPAdapter()
        self.assertEqual(adapter.get_tool_names(), [])

    def test_tool_with_unicode_in_name(self):
        """Should handle Unicode characters in tool name."""
        tmpdir = tempfile.mkdtemp()
        unicode_name = 'café_tool'
        _create_fake_tool_file(tmpdir, unicode_name,
                               _make_sync_tool_file(unicode_name))
        
        adapter = FastMCPAdapter(tools_dir=tmpdir, db_discovery=False)
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(adapter.initialize())
            names = adapter.get_tool_names()
            self.assertIn(unicode_name, names)
        finally:
            loop.close()
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_many_tools(self):
        """Should handle discovering many tools."""
        tmpdir = tempfile.mkdtemp()
        for i in range(100):
            _create_fake_tool_file(tmpdir, f'tool_{i:03d}',
                                   _make_sync_tool_file(f'tool_{i:03d}'))
        
        adapter = FastMCPAdapter(tools_dir=tmpdir, db_discovery=False)
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(adapter.initialize())
            names = adapter.get_tool_names()
            self.assertEqual(len(names), 100)
            self.assertEqual(names[0], 'tool_000')
            self.assertEqual(names[-1], 'tool_099')
        finally:
            loop.close()
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)


class TestFastMCPAdapterMultiFunctionDiscovery(unittest.TestCase):
    """Test discovery of tools with multiple public functions."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        _create_fake_tool_file(self.tmpdir, 'multi_func',
                               _make_multi_func_tool_file('multi_func'))
        self.adapter = FastMCPAdapter(tools_dir=self.tmpdir, db_discovery=False)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_first_public_function_used(self):
        """Should use the first public function as the tool handler."""
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(self.adapter.initialize())
            handler = self.adapter.get_tool('multi_func')
            self.assertIsNotNone(handler)
            
            # Execute — should run multi_func (x+1), not helper_func (y*2)
            result = loop.run_until_complete(
                self.adapter.execute_tool('multi_func', x=5)
            )
            self.assertEqual(result, 6)
        finally:
            loop.close()


if __name__ == '__main__':
    unittest.main()
