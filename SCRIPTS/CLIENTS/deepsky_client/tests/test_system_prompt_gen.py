"""
Unit tests for SystemPromptGenerator.

Part of DeepSky Self-Healing AI Client.
Tests: prompt generation from work orders, error contexts, tool definitions,
       edge cases, formatting.

NEVER USE pytest — always unittest!
"""

import asyncio
import json
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from system_prompt_generator import SystemPromptGenerator, BASE_SYSTEM_PROMPT


class TestSystemPromptGeneratorInit(unittest.TestCase):
    """Test initialization."""

    def test_init_default(self):
        """Test initialization with default DB path."""
        gen = SystemPromptGenerator()
        self.assertIsNotNone(gen)
        self.assertIn('Documentation.db', gen.db_path)

    def test_init_custom_path(self):
        """Test initialization with custom DB path."""
        gen = SystemPromptGenerator('/custom/path.db')
        self.assertEqual(gen.db_path, '/custom/path.db')


class TestSystemPromptGeneratorFromWorkOrder(unittest.TestCase):
    """Test generating prompts from work orders."""

    def setUp(self):
        self.gen = SystemPromptGenerator()

    def test_generate_basic(self):
        """Test basic prompt generation from work order."""
        order = {
            'id': 123,
            'priority': 2,
            'description': 'Fix the crashing bug in session_manager.py',
            'notes': '**Category:** code_bug\n**Component:** session_manager\n**Summary:** Null pointer\n**Stack Trace:**\n```\nFile "session_manager.py", line 42\n```',
            'created_at': '2026-06-07'
        }
        
        async def run_test():
            prompt = await self.gen.generate_for_work_order(order)
            self.assertIsNotNone(prompt)
            self.assertIn('Work Order Context', prompt)
            self.assertIn('#123', prompt)
            self.assertIn('Fix the crashing bug', prompt)
            self.assertIn('session_manager.py', prompt)
            self.assertIn('CORE HARD FAIL RULES', prompt)  # Base prompt included
            self.assertIn('NEVER USE pytest', prompt)
        
        asyncio.run(run_test())

    def test_generate_without_notes(self):
        """Test prompt generation with empty notes."""
        order = {
            'id': 456,
            'priority': 1,
            'description': 'Critical fix',
            'notes': '',
            'created_at': '2026-06-07'
        }
        
        async def run_test():
            prompt = await self.gen.generate_for_work_order(order)
            self.assertIn('#456', prompt)
            self.assertIn('Critical fix', prompt)
        
        asyncio.run(run_test())

    def test_generate_with_long_notes(self):
        """Test prompt generation with notes exceeding 4000 chars."""
        long_notes = 'X' * 5000
        order = {
            'id': 789,
            'priority': 3,
            'description': 'Long notes test',
            'notes': long_notes,
            'created_at': '2026-06-07'
        }
        
        async def run_test():
            prompt = await self.gen.generate_for_work_order(order)
            self.assertIn('(truncated)', prompt)  # Should be truncated
        
        asyncio.run(run_test())


class TestSystemPromptGeneratorFromError(unittest.TestCase):
    """Test generating prompts from error contexts."""

    def setUp(self):
        self.gen = SystemPromptGenerator()

    def test_generate_from_error(self):
        """Test prompt generation from error context."""
        error_context = {
            'type': 'ValueError',
            'component': 'api_client',
            'summary': 'Invalid response format from API',
            'timestamp': '2026-06-07T00:00:00Z',
            'stack_trace': 'Traceback (most recent call last):\n  File "api_client.py", line 100\n',
            'data_flow_path': ['API call', 'parse response', 'validate'],
            'session_state': {'messages': 15, 'tokens': 2000}
        }
        
        tool_defs = [
            {'name': 'database_query', 'description': 'Query a database'},
            {'name': 'read_files', 'description': 'Read file contents'}
        ]
        
        async def run_test():
            prompt = await self.gen.generate_from_error(error_context, tool_defs)
            self.assertIn('Auto-Detected Error Context', prompt)
            self.assertIn('ValueError', prompt)
            self.assertIn('api_client', prompt)
            self.assertIn('database_query', prompt)
        
        asyncio.run(run_test())

    def test_generate_from_error_empty_stack(self):
        """Test prompt generation with no stack trace."""
        error_context = {
            'type': 'Timeout',
            'component': 'network',
            'summary': 'Connection timed out',
            'stack_trace': '',
            'data_flow_path': [],
            'session_state': {}
        }
        
        async def run_test():
            prompt = await self.gen.generate_from_error(error_context, [])
            self.assertIn('No stack trace available', prompt)
        
        asyncio.run(run_test())


class TestSystemPromptGeneratorToolDefs(unittest.TestCase):
    """Test tool definition formatting."""

    def setUp(self):
        self.gen = SystemPromptGenerator()

    def test_get_tool_defs_formatted_empty(self):
        """Test formatting with no tools."""
        with patch.object(self.gen, '_get_tool_registry', return_value=[]):
            result = self.gen._get_tool_definitions_formatted()
            self.assertIn('Standard MCP tools', result)

    def test_get_tool_defs_formatted_categorized(self):
        """Test formatting with tools in categories."""
        mock_tools = [
            {'tool_name': 'read_files', 'description': 'Read files', 'category': 'files'},
            {'tool_name': 'database_query', 'description': 'Query DB', 'category': 'database'},
            {'tool_name': 'write_file', 'description': 'Write files', 'category': 'files'}
        ]
        
        with patch.object(self.gen, '_get_tool_registry', return_value=mock_tools):
            result = self.gen._get_tool_definitions_formatted()
            self.assertIn('FILES Tools', result)
            self.assertIn('DATABASE Tools', result)
            self.assertIn('read_files', result)
            self.assertIn('database_query', result)

    def test_get_tool_defs_without_descriptions(self):
        """Test formatting tools without descriptions."""
        mock_tools = [
            {'tool_name': 'minimal_tool', 'description': '', 'category': 'general'}
        ]
        
        with patch.object(self.gen, '_get_tool_registry', return_value=mock_tools):
            result = self.gen._get_tool_definitions_formatted()
            self.assertIn('minimal_tool', result)

    def test_get_tool_registry_cache(self):
        """Test tool registry caching."""
        # First call populates cache
        with patch('sqlite3.connect') as mock_connect:
            mock_cursor = MagicMock()
            mock_cursor.fetchall.return_value = [('tool1', 'desc1', 'cat1')]
            
            def mock_execute(sql, *args):
                mock_cursor.fetchall.return_value = [('t1', 'd1', 'c1')]
                return mock_cursor
            
            mock_cursor.execute = mock_execute
            mock_connect.return_value.cursor.return_value = mock_cursor
            
            tools1 = self.gen._get_tool_registry()
            # Second call should use cache
            tools2 = self.gen._get_tool_registry()
            self.assertEqual(len(tools1), 1)


class TestBaseSystemPrompt(unittest.TestCase):
    """Test the base system prompt template."""

    def test_base_prompt_has_required_sections(self):
        """Test base prompt has all required sections."""
        self.assertIn('CORE HARD FAIL RULES', BASE_SYSTEM_PROMPT)
        self.assertIn('{TOOL_DEFINITIONS}', BASE_SYSTEM_PROMPT)
        self.assertIn('NEVER USE pytest', BASE_SYSTEM_PROMPT)
        self.assertIn('SEQUENTIALTHINKING TOOL IS MANDATORY', BASE_SYSTEM_PROMPT)
        self.assertIn('get_dry_run', BASE_SYSTEM_PROMPT)
        self.assertIn('backup_audit', BASE_SYSTEM_PROMPT)
        self.assertIn('Documentation.db', BASE_SYSTEM_PROMPT)

    def test_base_prompt_placeholder_replacement(self):
        """Test placeholder replacement in base prompt."""
        filled = BASE_SYSTEM_PROMPT.replace('{TOOL_DEFINITIONS}', 'test tools')
        self.assertIn('test tools', filled)
        self.assertNotIn('{TOOL_DEFINITIONS}', filled)


if __name__ == '__main__':
    unittest.main()
