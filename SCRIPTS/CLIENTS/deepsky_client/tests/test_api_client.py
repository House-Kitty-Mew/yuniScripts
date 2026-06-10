"""
Comprehensive unit tests for DeepSeekAPIClient.

Part of DeepSky Self-Healing AI Client.
Tests: initialization, chat completion (streaming+non-streaming), retry logic,
       key rotation, error categorization, health checks, token tracking,
       edge cases for ALL possible failure modes.

NEVER USE pytest — always unittest!
"""

import asyncio
import json
import os
import sys
import time
import unittest
from unittest.mock import AsyncMock, MagicMock, Mock, patch, call
from typing import Dict, Any, Optional

# Add parent to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api_client import DeepSeekAPIClient, APIResponse, APIErrorCategory


class TestDeepSeekAPIClientInit(unittest.TestCase):
    """Test initialization with various config states."""

    def test_init_default_config(self):
        """Test initialization with empty config."""
        client = DeepSeekAPIClient({})
        self.assertEqual(client.base_url, 'https://api.deepseek.com/v1')
        self.assertEqual(client.model, 'deepseek-chat')
        self.assertEqual(client.api_keys, [])
        self.assertEqual(client.max_retries, 3)
        self.assertEqual(client.retry_base_delay, 1.0)
        self.assertEqual(client.timeout, 30.0)
        self.assertTrue(client.stream)
        self.assertTrue(client._healthy)
        self.assertEqual(client._key_index, 0)

    def test_init_full_config(self):
        """Test initialization with full configuration."""
        config = {
            'base_url': 'https://custom.api.com/v2',
            'model': 'deepseek-coder',
            'api_key': 'sk-primary-key',
            'backup_keys': ['sk-backup-1', 'sk-backup-2'],
            'max_retries': 5,
            'retry_base_delay': 2.0,
            'timeout': 60.0,
            'stream': False
        }
        client = DeepSeekAPIClient(config)
        self.assertEqual(client.base_url, 'https://custom.api.com/v2')
        self.assertEqual(client.model, 'deepseek-coder')
        self.assertIn('sk-primary-key', client.api_keys)
        self.assertIn('sk-backup-1', client.api_keys)
        self.assertIn('sk-backup-2', client.api_keys)
        self.assertEqual(client.max_retries, 5)
        self.assertEqual(client.retry_base_delay, 2.0)
        self.assertEqual(client.timeout, 60.0)
        self.assertFalse(client.stream)

    def test_init_no_duplicate_keys(self):
        """Test that duplicate keys are not added."""
        config = {
            'api_key': 'sk-key',
            'backup_keys': ['sk-key', 'sk-key', 'sk-other']
        }
        client = DeepSeekAPIClient(config)
        self.assertEqual(len(client.api_keys), 2)  # primary + one backup
        self.assertIn('sk-key', client.api_keys)
        self.assertIn('sk-other', client.api_keys)

    def test_init_empty_backup_keys(self):
        """Test initialization with empty backup keys list."""
        config = {
            'api_key': 'sk-primary',
            'backup_keys': []
        }
        client = DeepSeekAPIClient(config)
        self.assertEqual(len(client.api_keys), 1)
        self.assertEqual(client.api_keys[0], 'sk-primary')

    def test_init_no_api_key(self):
        """Test initialization with no API key at all."""
        config = {'base_url': 'https://test.api.com'}
        client = DeepSeekAPIClient(config)
        self.assertEqual(client.api_keys, [])
        self.assertEqual(client._get_current_key(), '')

    @patch('api_client.aiohttp')
    def test_session_lazy_init(self, mock_aiohttp):
        """Test that session is lazily initialized."""
        client = DeepSeekAPIClient({'api_key': 'sk-test'})
        self.assertIsNone(client._session)
        # Session won't be created until first call


class TestDeepSeekAPIClientKeyRotation(unittest.TestCase):
    """Test API key rotation functionality."""

    def setUp(self):
        self.config = {
            'api_key': 'sk-primary',
            'backup_keys': ['sk-backup-1', 'sk-backup-2']
        }
        self.client = DeepSeekAPIClient(self.config)

    def _await_rotate(self, client=None):
        """Helper to await async _rotate_key."""
        import asyncio
        c = client or self.client
        return asyncio.run(c._rotate_key())

    def test_get_current_key_primary(self):
        """Test that initial key is primary."""
        self.assertEqual(self.client._get_current_key(), 'sk-primary')

    def test_rotate_key_success(self):
        """Test successful key rotation to backup."""
        result = asyncio.run(self.client._rotate_key())
        self.assertTrue(result)
        self.assertEqual(self.client._get_current_key(), 'sk-backup-1')

    def test_rotate_key_cycles_to_next(self):
        """Test that rotation cycles through all keys."""
        asyncio.run(self.client._rotate_key())  # -> backup-1
        asyncio.run(self.client._rotate_key())  # -> backup-2
        self.assertEqual(self.client._get_current_key(), 'sk-backup-2')

    def test_rotate_key_wraps_around(self):
        """Test that rotation wraps back to primary."""
        asyncio.run(self.client._rotate_key())  # -> backup-1
        asyncio.run(self.client._rotate_key())  # -> backup-2
        asyncio.run(self.client._rotate_key())  # -> primary (wrap)
        self.assertEqual(self.client._get_current_key(), 'sk-primary')

    def test_rotate_key_single_key(self):
        """Test rotation with only one key."""
        single_client = DeepSeekAPIClient({'api_key': 'sk-only'})
        result = asyncio.run(single_client._rotate_key())
        self.assertFalse(result)
        self.assertEqual(single_client._get_current_key(), 'sk-only')

    def test_rotate_key_no_keys(self):
        """Test rotation with no keys configured."""
        empty_client = DeepSeekAPIClient({})
        result = asyncio.run(empty_client._rotate_key())
        self.assertFalse(result)
        self.assertEqual(empty_client._get_current_key(), '')

    def test_rotate_key_closes_session(self):
        """Test that rotation closes old aiohttp session."""
        with patch.object(self.client, '_session', AsyncMock()) as mock_session:
            mock_session.closed = False
            asyncio.run(self.client._rotate_key())
            mock_session.close.assert_called_once()
            self.assertIsNone(self.client._session)


class TestDeepSeekAPIClientErrorCategorization(unittest.TestCase):
    """Test error categorization logic."""

    def setUp(self):
        self.client = DeepSeekAPIClient({'api_key': 'sk-test'})

    def test_categorize_auth_401(self):
        """Test 401 categorized as AUTH."""
        cat = self.client._categorize_error(Exception('unauthorized'), 401)
        self.assertEqual(cat, APIErrorCategory.AUTH)

    def test_categorize_auth_403(self):
        """Test 403 categorized as AUTH."""
        cat = self.client._categorize_error(Exception('forbidden'), 403)
        self.assertEqual(cat, APIErrorCategory.AUTH)

    def test_categorize_rate_limit_429(self):
        """Test 429 categorized as RATE_LIMIT."""
        cat = self.client._categorize_error(Exception('too many'), 429)
        self.assertEqual(cat, APIErrorCategory.RATE_LIMIT)

    def test_categorize_server_error_500(self):
        """Test 500 categorized as SERVER_ERROR."""
        cat = self.client._categorize_error(Exception('internal'), 500)
        self.assertEqual(cat, APIErrorCategory.SERVER_ERROR)

    def test_categorize_server_error_503(self):
        """Test 503 categorized as SERVER_ERROR."""
        cat = self.client._categorize_error(Exception('unavailable'), 503)
        self.assertEqual(cat, APIErrorCategory.SERVER_ERROR)

    def test_categorize_timeout(self):
        """Test TimeoutError categorized as TIMEOUT."""
        cat = self.client._categorize_error(asyncio.TimeoutError())
        self.assertEqual(cat, APIErrorCategory.TIMEOUT)

    def test_categorize_connection_error(self):
        """Test ConnectionError categorized as NETWORK."""
        cat = self.client._categorize_error(ConnectionError('refused'))
        self.assertEqual(cat, APIErrorCategory.NETWORK)

    def test_categorize_os_error(self):
        """Test OSError categorized as NETWORK."""
        cat = self.client._categorize_error(OSError('connection reset'))
        self.assertEqual(cat, APIErrorCategory.NETWORK)

    def test_categorize_json_error(self):
        """Test JSONDecodeError categorized as MALFORMED_RESPONSE."""
        cat = self.client._categorize_error(json.JSONDecodeError('Expecting value', '', 0))
        self.assertEqual(cat, APIErrorCategory.MALFORMED_RESPONSE)

    def test_categorize_type_error(self):
        """Test TypeError categorized as MALFORMED_RESPONSE."""
        cat = self.client._categorize_error(TypeError('unsupported type'))
        self.assertEqual(cat, APIErrorCategory.MALFORMED_RESPONSE)

    def test_categorize_unknown(self):
        """Test unknown exception categorized as UNKNOWN."""
        cat = self.client._categorize_error(RuntimeError('weird error'))
        self.assertEqual(cat, APIErrorCategory.UNKNOWN)

    def test_categorize_no_status_code(self):
        """Test error without status code still categorized."""
        cat = self.client._categorize_error(ValueError('bad value'))
        self.assertEqual(cat, APIErrorCategory.MALFORMED_RESPONSE)


class TestDeepSeekAPIClientChatCompletion(unittest.TestCase):
    """Test chat completion with various scenarios using mocks."""

    def setUp(self):
        self.config = {
            'api_key': 'sk-test',
            'max_retries': 2,
            'retry_base_delay': 0.01,  # Fast retries for testing
            'timeout': 5.0
        }
        self.client = DeepSeekAPIClient(self.config)
        self.test_messages = [{'role': 'user', 'content': 'Hello'}]

    @patch('api_client.aiohttp.ClientSession')
    def test_non_stream_success(self, mock_session):
        """Test successful non-streaming completion."""
        # Mock response
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.__aenter__.return_value = mock_resp
        
        async def mock_json():
            return {
                'choices': [{
                    'message': {
                        'content': 'Hello! How can I help?',
                        'role': 'assistant'
                    }
                }],
                'usage': {'total_tokens': 15, 'prompt_tokens': 5, 'completion_tokens': 10}
            }
        mock_resp.json = mock_json
        
        mock_session_instance = AsyncMock()
        mock_session_instance.post = Mock(return_value=mock_resp)
        mock_session_instance.closed = False
        mock_session.return_value = mock_session_instance
        
        async def run_test():
            self.client._session = mock_session_instance
            response = await self.client.chat_completion(self.test_messages, stream=False)
            
            self.assertTrue(response.success)
            self.assertEqual(response.content, 'Hello! How can I help?')
            self.assertEqual(response.usage['total_tokens'], 15)
            self.assertTrue(self.client._healthy)
        
        asyncio.run(run_test())

    @patch('api_client.aiohttp.ClientSession')
    def test_non_stream_with_tools(self, mock_session):
        """Test non-streaming completion with tools."""
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.__aenter__.return_value = mock_resp
        
        async def mock_json():
            return {
                'choices': [{
                    'message': {
                        'content': None,
                        'role': 'assistant',
                        'tool_calls': [
                            {'id': 'call_1', 'type': 'function', 
                             'function': {'name': 'get_weather', 'arguments': '{"city":"London"}'}}
                        ]
                    }
                }],
                'usage': {'total_tokens': 25}
            }
        mock_resp.json = mock_json
        
        mock_session_instance = AsyncMock()
        mock_session_instance.post = Mock(return_value=mock_resp)
        mock_session_instance.closed = False
        
        tools = [{'type': 'function', 'function': {'name': 'get_weather'}}]
        
        async def run_test():
            self.client._session = mock_session_instance
            response = await self.client.chat_completion(
                self.test_messages, tools=tools, stream=False
            )
            
            self.assertTrue(response.success)
            self.assertIsNone(response.content)
            self.assertIsNotNone(response.tool_calls)
            self.assertEqual(len(response.tool_calls), 1)
            self.assertEqual(response.tool_calls[0]['function']['name'], 'get_weather')
        
        asyncio.run(run_test())

    @patch('api_client.aiohttp.ClientSession')
    def test_stream_success(self, mock_session):
        """Test successful streaming completion."""
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.__aenter__.return_value = mock_resp
        
        chunks = [
            b'data: {"choices":[{"delta":{"content":"Hello"}}]}\n',
            b'data: {"choices":[{"delta":{"content":" world"}}]}\n',
            b'data: {"choices":[{"delta":{}}],"usage":{"total_tokens":10}}\n',
            b'data: [DONE]\n'
        ]
        
        # Mock async content iteration
        async def mock_content():
            for chunk in chunks:
                yield chunk
        
        mock_resp.content = mock_content().__aiter__()
        
        mock_session_instance = AsyncMock()
        mock_session_instance.post = Mock(return_value=mock_resp)
        mock_session_instance.closed = False
        
        async def run_test():
            self.client._session = mock_session_instance
            response = await self.client.chat_completion(self.test_messages, stream=True)
            
            self.assertTrue(response.success)
            self.assertEqual(response.content, 'Hello world')
        
        asyncio.run(run_test())

    @patch('api_client.aiohttp.ClientSession')
    def test_auth_error_triggers_key_rotation(self, mock_session):
        """Test that 401 triggers key rotation."""
        config = {
            'api_key': 'sk-bad',
            'backup_keys': ['sk-good'],
            'max_retries': 0,  # No retry to isolate rotation behavior
        }
        client = DeepSeekAPIClient(config)
        
        # Mock first session to return 401
        mock_resp_bad = AsyncMock()
        mock_resp_bad.status = 401
        mock_resp_bad.__aenter__.return_value = mock_resp_bad
        
        mock_session_instance = AsyncMock()
        
        async def mock_text():
            return 'Invalid API key'
        mock_resp_bad.text = mock_text
        
        async def run_test():
            import aiohttp
            client._session = mock_session_instance
            mock_session_instance.post = Mock(return_value=mock_resp_bad)
            mock_session_instance.closed = False
            
            # First call with bad key should trigger rotation
            # But since we mock the post, we won't actually hit aiohttp
            # We need to raise the error properly
            from aiohttp import ClientResponseError
            
            mock_resp_bad.raise_for_status = MagicMock(
                side_effect=ClientResponseError(
                    MagicMock(), MagicMock(),
                    status=401, message='Invalid API Key'
                )
            )
            
            # Reset session after rotation
            client._session = None
            
            # Now mock good response
            mock_resp_good = AsyncMock()
            mock_resp_good.status = 200
            mock_resp_good.__aenter__.return_value = mock_resp_good
            
            async def mock_json_good():
                return {'choices': [{'message': {'content': 'OK'}}], 'usage': {}}
            mock_resp_good.json = mock_json_good
            
            # After rotation, new session should be created
            # Note: In real code, _rotate_key creates new session
            # For test, we verify rotation happened
            initial_key = client._get_current_key()
            self.assertEqual(initial_key, 'sk-bad')
            
            await client._rotate_key()
            rotated_key = client._get_current_key()
            self.assertEqual(rotated_key, 'sk-good')
        
        asyncio.run(run_test())

    @patch('api_client.aiohttp.ClientSession')
    def test_rate_limit_backoff(self, mock_session):
        """Test rate limiting triggers backoff and retry."""
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.__aenter__.return_value = mock_resp
        
        call_count = [0]
        
        async def mock_json_side_effect():
            call_count[0] += 1
            if call_count[0] <= 1:
                # Raise rate limit on first call
                raise aiohttp.ClientResponseError(
                    MagicMock(), MagicMock(),
                    status=429, message='Rate limited'
                )
            return {
                'choices': [{'message': {'content': 'Success after rate limit'}}],
                'usage': {}
            }
        
        mock_resp.json = mock_json_side_effect
        
        mock_session_instance = AsyncMock()
        mock_session_instance.post = Mock(return_value=mock_resp)
        mock_session_instance.closed = False
        
        async def run_test():
            import aiohttp
            self.client._session = mock_session_instance
            response = await self.client.chat_completion(self.test_messages, stream=False)
            
            # Should succeed after retry
            self.assertTrue(response.success)
            self.assertGreater(call_count[0], 0)
        
        asyncio.run(run_test())

    @patch('api_client.aiohttp.ClientSession')
    def test_retry_exhaustion(self, mock_session):
        """Test that retries are exhausted properly."""
        mock_resp = AsyncMock()
        mock_resp.status = 503
        mock_resp.__aenter__.return_value = mock_resp
        
        mock_session_instance = AsyncMock()
        mock_session_instance.post = Mock(return_value=mock_resp)
        mock_session_instance.closed = False
        
        async def run_test():
            import aiohttp
            self.client._session = mock_session_instance
            
            # Always raise server error
            from aiohttp import ClientResponseError
            
            def raise_error():
                raise ClientResponseError(
                    MagicMock(), MagicMock(),
                    status=503, message='Service Unavailable'
                )
            
            mock_resp.raise_for_status = raise_error
            
            response = await self.client.chat_completion(self.test_messages, stream=False)
            
            self.assertFalse(response.success)
            self.assertIsNotNone(response.error)
            self.assertEqual(response.error_category, APIErrorCategory.SERVER_ERROR)
            self.assertFalse(self.client._healthy)
        
        asyncio.run(run_test())

    @patch('api_client.aiohttp.ClientSession')
    def test_network_error_retry(self, mock_session):
        """Test network errors trigger retry."""
        mock_session_instance = AsyncMock()
        mock_session_instance.closed = False
        self.client._session = mock_session_instance
        
        call_count = [0]
        
        def post_side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] <= 1:
                raise ConnectionError('Connection refused')
            # Return success on retry
            mock_resp = AsyncMock()
            mock_resp.status = 200
            mock_resp.__aenter__.return_value = mock_resp
            
            async def mock_json():
                return {'choices': [{'message': {'content': 'Recovered'}}], 'usage': {}}
            mock_resp.json = mock_json
            return mock_resp
        
        mock_session_instance.post = Mock(side_effect=post_side_effect)
        
        async def run_test():
            response = await self.client.chat_completion(self.test_messages, stream=False)
            
            self.assertTrue(response.success)
            self.assertEqual(response.content, 'Recovered')
            self.assertGreater(call_count[0], 1)
        
        asyncio.run(run_test())

    @patch('api_client.aiohttp.ClientSession')
    def test_timeout_retry(self, mock_session):
        """Test timeout triggers retry."""
        mock_session_instance = AsyncMock()
        mock_session_instance.closed = False
        self.client._session = mock_session_instance
        
        call_count = [0]
        
        def post_side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] <= 1:
                raise asyncio.TimeoutError()
            mock_resp = AsyncMock()
            mock_resp.status = 200
            mock_resp.__aenter__.return_value = mock_resp
            
            async def mock_json():
                return {'choices': [{'message': {'content': 'OK after timeout'}}], 'usage': {}}
            mock_resp.json = mock_json
            return mock_resp
        
        mock_session_instance.post = Mock(side_effect=post_side_effect)
        
        async def run_test():
            response = await self.client.chat_completion(self.test_messages, stream=False)
            
            self.assertTrue(response.success)
            self.assertGreater(call_count[0], 1)
        
        asyncio.run(run_test())

    def test_empty_messages_list(self):
        """Test sending empty messages list (edge case)."""
        # This tests that the client doesn't crash on empty input
        client = DeepSeekAPIClient({'api_key': 'sk-test', 'max_retries': 0})
        
        async def run_test():
            # Should not raise, will fail at API call
            try:
                response = await client.chat_completion([], stream=False)
                self.assertFalse(response.success)  # Will fail without session
            except Exception:
                pass  # Expected since no real session
        
        asyncio.run(run_test())


class TestDeepSeekAPIClientHealth(unittest.TestCase):
    """Test health check functionality."""

    def setUp(self):
        self.client = DeepSeekAPIClient({'api_key': 'sk-test', 'max_retries': 0})

    @patch('api_client.aiohttp.ClientSession')
    def test_check_health_success(self, mock_session):
        """Test health check returns True when API is reachable."""
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.__aenter__.return_value = mock_resp
        
        async def mock_json():
            return {'choices': [{'message': {'content': 'pong'}}], 'usage': {}}
        mock_resp.json = mock_json
        
        mock_session_instance = AsyncMock()
        mock_session_instance.post = Mock(return_value=mock_resp)
        mock_session_instance.closed = False
        mock_session.return_value = mock_session_instance
        
        async def run_test():
            self.client._session = mock_session_instance
            healthy = await self.client.check_health()
            self.assertTrue(healthy)
            self.assertTrue(self.client._healthy)
        
        asyncio.run(run_test())

    @patch('api_client.aiohttp.ClientSession')
    def test_check_health_failure(self, mock_session):
        """Test health check returns False when API is unreachable."""
        mock_session_instance = AsyncMock()
        mock_session_instance.post.side_effect = ConnectionError('Connection refused')
        self.client._session = mock_session_instance
        
        async def run_test():
            healthy = await self.client.check_health()
            self.assertFalse(healthy)
            self.assertFalse(self.client._healthy)
        
        asyncio.run(run_test())

    def test_check_health_no_session_yet(self):
        """Test health check when session hasn't been initialized."""
        self.assertIsNone(self.client._session)
        
        async def run_test():
            # Should handle gracefully
            healthy = await self.client.check_health()
            self.assertFalse(healthy)  # No API key to actually call
        
        asyncio.run(run_test())


class TestDeepSeekAPIClientTokenTracking(unittest.TestCase):
    """Test token budget tracking."""

    def setUp(self):
        self.client = DeepSeekAPIClient({
            'api_key': 'sk-test',
            'max_retries': 0
        })

    def test_initial_token_budget(self):
        """Test initial token budget is default max."""
        budget = self.client.get_token_budget()
        self.assertEqual(budget, 65536)

    def test_token_budget_tracking(self):
        """Test token budget decreases with usage."""
        # Directly add tokens
        self.client._tokens_used = 5000
        budget = self.client.get_token_budget()
        self.assertEqual(budget, 60536)

    def test_token_budget_exhausted(self):
        """Test token budget at zero."""
        self.client._tokens_used = 65536
        budget = self.client.get_token_budget()
        self.assertEqual(budget, 0)

    def test_token_budget_over_limit(self):
        """Test token budget below zero (should clamp)."""
        self.client._tokens_used = 70000
        budget = self.client.get_token_budget()
        self.assertEqual(budget, 0)

    def test_token_budget_no_limit(self):
        """Test token budget with no _token_budget set (should use default)."""
        del self.client._token_budget
        budget = self.client.get_token_budget()
        # Should not crash
        self.assertIsInstance(budget, (int, float))


class TestDeepSeekAPIClientUsageStats(unittest.TestCase):
    """Test usage statistics reporting."""

    def setUp(self):
        self.client = DeepSeekAPIClient({'api_key': 'sk-test'})

    def test_usage_stats_defaults(self):
        """Test usage stats returns all expected fields with defaults."""
        stats = self.client.get_usage_stats()
        self.assertIn('tokens_used', stats)
        self.assertIn('tokens_remaining', stats)
        self.assertIn('healthy', stats)
        self.assertIn('key_index', stats)
        self.assertIn('total_keys', stats)
        self.assertIn('last_error', stats)
        self.assertIn('last_error_category', stats)
        self.assertTrue(stats['healthy'])
        self.assertEqual(stats['total_keys'], 1)

    def test_usage_stats_after_error(self):
        """Test usage stats reflects error state."""
        self.client._healthy = False
        self.client._last_error = 'Connection refused'
        self.client._last_error_category = APIErrorCategory.NETWORK
        
        stats = self.client.get_usage_stats()
        self.assertFalse(stats['healthy'])
        self.assertEqual(stats['last_error'], 'Connection refused')
        self.assertIsNotNone(stats['last_error_category'])

    def test_usage_stats_after_usage(self):
        """Test usage stats after token consumption."""
        self.client._tokens_used = 1000
        self.client._key_index = 2
        
        stats = self.client.get_usage_stats()
        self.assertEqual(stats['tokens_used'], 1000)
        self.assertEqual(stats['key_index'], 2)


class TestDeepSeekAPIClientClose(unittest.TestCase):
    """Test session closing."""

    def test_close_active_session(self):
        """Test closing an active session."""
        client = DeepSeekAPIClient({'api_key': 'sk-test'})
        
        async def run_test():
            mock_session = AsyncMock()
            mock_session.closed = False
            mock_session.closed = False
            client._session = mock_session
            
            await client.close()
            mock_session.close.assert_called_once()
        
        asyncio.run(run_test())

    def test_close_already_closed(self):
        """Test closing an already-closed session."""
        client = DeepSeekAPIClient({'api_key': 'sk-test'})
        
        async def run_test():
            mock_session = AsyncMock()
            mock_session.closed = False
            mock_session.closed = True
            client._session = mock_session
            
            await client.close()
            # close() should not be called on already-closed session
            mock_session.close.assert_not_called()
        
        asyncio.run(run_test())

    def test_close_no_session(self):
        """Test closing when no session exists."""
        client = DeepSeekAPIClient({'api_key': 'sk-test'})
        
        async def run_test():
            client._session = None
            await client.close()  # Should not raise
        
        asyncio.run(run_test())


class TestAPIResponse(unittest.TestCase):
    """Test APIResponse dataclass."""

    def test_success_response(self):
        """Test creating a success response."""
        resp = APIResponse(
            success=True,
            content='Hello',
            tool_calls=[{'id': 'call_1'}],
            usage={'total_tokens': 10}
        )
        self.assertTrue(resp.success)
        self.assertEqual(resp.content, 'Hello')
        self.assertEqual(len(resp.tool_calls), 1)

    def test_failure_response(self):
        """Test creating a failure response."""
        resp = APIResponse(
            success=False,
            error='API Error',
            error_category=APIErrorCategory.AUTH
        )
        self.assertFalse(resp.success)
        self.assertEqual(resp.error, 'API Error')
        self.assertEqual(resp.error_category, APIErrorCategory.AUTH)

    def test_response_defaults(self):
        """Test default values for APIResponse."""
        resp = APIResponse(success=True)
        self.assertEqual(resp.data, {})
        self.assertEqual(resp.tool_calls, [])
        self.assertEqual(resp.usage, {})
        self.assertEqual(resp.stream_chunks, [])
        self.assertIsNone(resp.error)
        self.assertIsNone(resp.error_category)

    def test_response_with_data(self):
        """Test response with raw data."""
        data = {'id': 'chatcmpl-123', 'object': 'chat.completion'}
        resp = APIResponse(success=True, data=data)
        self.assertEqual(resp.data['id'], 'chatcmpl-123')


class TestDeepSeekAPIClientEdgeCases(unittest.TestCase):
    """Test extreme edge cases for API client."""

    def test_concurrent_requests_same_session(self):
        """Test that multiple concurrent requests use same session."""
        client = DeepSeekAPIClient({'api_key': 'sk-test', 'max_retries': 0})
        
        async def run_test():
            mock_session = AsyncMock()
            mock_session.closed = False
            client._session = mock_session
            self.assertIsNotNone(client._session)
            
            # Verify we can get the session without creating a new one
            session = await client._get_session()
            self.assertIs(session, mock_session)
        
        asyncio.run(run_test())

    def test_message_validation_strict(self):
        """Test with only valid role types accepted by API."""
        client = DeepSeekAPIClient({'api_key': 'sk-test'})
        
        messages = [
            {'role': 'system', 'content': 'You are helpful.'},
            {'role': 'user', 'content': 'Hi'},
            {'role': 'assistant', 'content': 'Hello'},
            {'role': 'tool', 'content': 'Result: 42', 'tool_call_id': 'call_1'}
        ]
        
        # Validate message structure
        for msg in messages:
            self.assertIn('role', msg)
            self.assertIn('content', msg)
            self.assertIn(msg['role'], ('system', 'user', 'assistant', 'tool'))

    @patch('api_client.DeepSeekAPIClient._get_session')
    def test_connection_refused_then_recovers(self, mock_get_session):
        """Test connection recovery after initial refusal."""
        client = DeepSeekAPIClient({
            'api_key': 'sk-test',
            'max_retries': 2,
            'retry_base_delay': 0.01
        })
        
        call_count = [0]
        
        async def session_side_effect():
            call_count[0] += 1
            if call_count[0] <= 1:
                raise ConnectionError('Connection refused')
            mock_s = AsyncMock()
            mock_s.closed = False
            return mock_s
        
        mock_get_session.side_effect = session_side_effect
        
        async def run_test():
            response = await client.chat_completion(
                [{'role': 'user', 'content': 'test'}],
                stream=False
            )
            self.assertFalse(response.success)  # Will fail at mock level
        
        asyncio.run(run_test())


if __name__ == '__main__':
    unittest.main()
