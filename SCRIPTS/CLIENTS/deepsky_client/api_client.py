"""
DeepSeek API Client — Async wrapper with retry logic, key rotation, token tracking.

Part of DeepSky Self-Healing AI Client (YuniScript).
Spec: DEEPSKY_SELF_HEALING_ECOSYSTEM_SPEC.md (in YuniScripts base)
"""

import asyncio
import aiohttp
import json
import logging
import time
from enum import Enum, auto
from typing import Optional, Dict, Any, List, AsyncIterator

logger = logging.getLogger(__name__)


class APIErrorCategory(Enum):
    """Categorized error types for self-healing work order generation."""
    NETWORK = auto()
    AUTH = auto()
    RATE_LIMIT = auto()
    SERVER_ERROR = auto()
    TIMEOUT = auto()
    MALFORMED_RESPONSE = auto()
    UNKNOWN = auto()


class APIResponse:
    """Unified response from DeepSeek API."""
    
    def __init__(self, success: bool, data: Optional[Dict] = None,
                 error: Optional[str] = None, error_category: Optional[APIErrorCategory] = None,
                 content: Optional[str] = None, tool_calls: Optional[List[Dict]] = None,
                 usage: Optional[Dict] = None, stream_chunks: Optional[List[str]] = None):
        self.success = success
        self.data = data or {}
        self.error = error
        self.error_category = error_category
        self.content = content
        self.tool_calls = tool_calls or []
        self.usage = usage or {}
        self.stream_chunks = stream_chunks or []


class DeepSeekAPIClient:
    """Async DeepSeek API client with retry, key rotation, and health checks."""

    def __init__(self, config: Dict[str, Any]):
        self.base_url = config.get('base_url', 'https://api.deepseek.com/v1')
        self.model = config.get('model', 'deepseek-chat')
        self.api_keys = []
        primary_key = config.get('api_key', '')
        if primary_key:
            self.api_keys.append(primary_key)
        backup_keys = config.get('backup_keys', [])
        self.api_keys.extend([k for k in backup_keys if k and k != primary_key])
        
        self.max_retries = config.get('max_retries', 3)
        self.retry_base_delay = config.get('retry_base_delay', 1.0)
        self.timeout = config.get('timeout', 30.0)
        self.stream = config.get('stream', True)
        
        self._key_index = 0
        self._token_budget = 65536  # Configurable max tokens
        self._tokens_used = 0
        self._session = None
        self._healthy = True
        self._last_error = None
        self._last_error_category = None
        
    async def _get_session(self):
        """Lazy-init aiohttp session."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self.timeout),
                headers={
                    'Content-Type': 'application/json',
                    'Authorization': f'Bearer {self._get_current_key()}'
                }
            )
        return self._session
    
    def _get_current_key(self) -> str:
        """Get current active API key."""
        if not self.api_keys:
            return ''
        return self.api_keys[self._key_index % len(self.api_keys)]
    
    async def _rotate_key(self) -> bool:
        """Rotate to next available API key. Returns True if rotation happened."""
        if len(self.api_keys) <= 1:
            logger.warning("No backup API keys available for rotation")
            return False
        self._key_index = (self._key_index + 1) % len(self.api_keys)
        new_key = self._get_current_key()
        logger.info(f"Rotated to API key index {self._key_index}")
        # Recreate session with new auth header
        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None
        return True
    
    def _categorize_error(self, exception: Exception, status_code: Optional[int] = None) -> APIErrorCategory:
        """Categorize error for work order generation."""
        if status_code == 401 or status_code == 403:
        
            return APIErrorCategory.AUTH
        
        elif status_code == 429:
            return APIErrorCategory.RATE_LIMIT
        elif isinstance(status_code, int) and status_code >= 500:
            return APIErrorCategory.SERVER_ERROR
        elif isinstance(exception, asyncio.TimeoutError):
            return APIErrorCategory.TIMEOUT
        elif isinstance(exception, (ConnectionError, OSError)):
            return APIErrorCategory.NETWORK
        elif isinstance(exception, (json.JSONDecodeError, TypeError, ValueError)):
            return APIErrorCategory.MALFORMED_RESPONSE
        else:
            return APIErrorCategory.UNKNOWN
    
    async def chat_completion(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict]] = None,
        stream: Optional[bool] = None
    ) -> APIResponse:
        """
        Send a chat completion request to DeepSeek API.
        
        Args:
            messages: List of message dicts [{'role': 'user', 'content': '...'}]
            tools: Optional list of tool definitions
            stream: Override default streaming behavior
            
        Returns:
            APIResponse with content, tool_calls, usage data
        """
        use_stream = stream if stream is not None else self.stream
        last_error = None
        last_category = None
        
        for attempt in range(self.max_retries + 1):
            try:
                session = await self._get_session()
                
                payload = {
                    'model': self.model,
                    'messages': messages,
                    'stream': use_stream
                }
                if tools:
                    payload['tools'] = tools
                
                start_time = time.time()
                
                if use_stream:
                    return await self._stream_completion(session, payload)
                else:
                    return await self._non_stream_completion(session, payload, start_time)
                    
            except aiohttp.ClientResponseError as e:
                last_category = self._categorize_error(e, e.status)
                last_error = f"HTTP {e.status}: {e.message}"
                
                if e.status == 401:
                    # Auth failure — try key rotation
                    if await self._rotate_key():
                        logger.info("Key rotated on auth failure, retrying...")
                        continue
                    return APIResponse(
                        success=False,
                        error=last_error,
                        error_category=last_category
                    )
                elif e.status == 429:
                    # Rate limited — exponential backoff
                    delay = self.retry_base_delay * (2 ** attempt)
                    logger.warning(f"Rate limited, backing off {delay}s (attempt {attempt+1})")
                    await asyncio.sleep(delay)
                    continue
                elif isinstance(e.status, int) and e.status >= 500:
                    # Server error — retry
                    if attempt < self.max_retries:
                        delay = self.retry_base_delay * (2 ** attempt)
                        logger.warning(f"Server error {e.status}, retrying in {delay}s")
                        await asyncio.sleep(delay)
                        continue
                    
            except asyncio.TimeoutError:
                last_category = APIErrorCategory.TIMEOUT
                last_error = f"Request timeout after {self.timeout}s"
                if attempt < self.max_retries:
                    delay = self.retry_base_delay * (2 ** attempt)
                    logger.warning(f"Timeout, retrying in {delay}s (attempt {attempt+1})")
                    await asyncio.sleep(delay)
                    continue
                    
            except (ConnectionError, OSError) as e:
                last_category = APIErrorCategory.NETWORK
                last_error = f"Network error: {str(e)}"
                if attempt < self.max_retries:
                    delay = self.retry_base_delay * (2 ** attempt)
                    logger.warning(f"Network error, retrying in {delay}s")
                    await asyncio.sleep(delay)
                    continue
                    
            except Exception as e:
                last_category = self._categorize_error(e)
                last_error = str(e)
                if attempt < self.max_retries:
                    delay = self.retry_base_delay * (2 ** attempt)
                    logger.warning(f"Error: {e}, retrying in {delay}s")
                    await asyncio.sleep(delay)
                    continue
        
        # All retries exhausted
        self._healthy = False
        self._last_error = last_error
        self._last_error_category = last_category
        logger.error(f"API call failed after {self.max_retries+1} attempts: {last_error}")
        return APIResponse(
            success=False,
            error=last_error,
            error_category=last_category
        )
    
    async def _stream_completion(self, session, payload: Dict) -> APIResponse:
        """Handle streaming completion response."""
        url = f"{self.base_url}/chat/completions"
        content_parts = []
        tool_calls = []
        usage = {}
        
        async with session.post(url, json=payload) as resp:
            if resp.status != 200:
                raise aiohttp.ClientResponseError(
                    resp.request_info, resp.history,
                    status=resp.status, message=await resp.text()
                )
            
            async for line in resp.content:
                line = line.decode('utf-8').strip()
                if not line or line == 'data: [DONE]':
                    continue
                if line.startswith('data: '):
                    try:
                        chunk = json.loads(line[6:])
                        delta = chunk.get('choices', [{}])[0].get('delta', {})
                        
                        if delta.get('content'):
                            content_parts.append(delta['content'])
                        if delta.get('tool_calls'):
                            tool_calls.append(delta['tool_calls'])
                        if chunk.get('usage'):
                            usage = chunk['usage']
                    except (json.JSONDecodeError, IndexError, KeyError):
                        continue
        
        full_content = ''.join(content_parts) if content_parts else None
        
        # Track token usage
        if usage:
            self._tokens_used += usage.get('total_tokens', 0)
        
        return APIResponse(
            success=True,
            content=full_content,
            tool_calls=tool_calls if tool_calls else None,
            usage=usage,
            stream_chunks=content_parts
        )
    
    async def _non_stream_completion(self, session, payload: Dict, start_time: float) -> APIResponse:
        """Handle non-streaming completion response."""
        payload['stream'] = False
        url = f"{self.base_url}/chat/completions"
        
        async with session.post(url, json=payload) as resp:
            if resp.status != 200:
                raise aiohttp.ClientResponseError(
                    resp.request_info, resp.history,
                    status=resp.status, message=await resp.text()
                )
            
            data = await resp.json()
            
            elapsed = time.time() - start_time
            logger.debug(f"API response in {elapsed:.2f}s")
            
            choice = data.get('choices', [{}])[0]
            message = choice.get('message', {})
            content = message.get('content')
            tool_calls = message.get('tool_calls')
            usage = data.get('usage', {})
            
            if usage:
                self._tokens_used += usage.get('total_tokens', 0)
            
            return APIResponse(
                success=True,
                data=data,
                content=content,
                tool_calls=tool_calls,
                usage=usage
            )
    
    async def check_health(self) -> bool:
        """Check if the API is reachable and key is valid."""
        resp = await self.chat_completion(
            messages=[{'role': 'user', 'content': 'ping'}],
            stream=False
        )
        self._healthy = resp.success
        return resp.success
    def get_token_budget(self) -> int:
        """Get remaining token budget for current session."""
        budget = getattr(self, '_token_budget', 65536)
        return max(0, budget - self._tokens_used)
    
    def get_usage_stats(self) -> Dict:
        """Get API usage statistics."""
        return {
            'tokens_used': self._tokens_used,
            'tokens_remaining': self.get_token_budget(),
            'healthy': self._healthy,
            'key_index': self._key_index,
            'total_keys': len(self.api_keys),
            'last_error': self._last_error,
            'last_error_category': str(self._last_error_category) if self._last_error_category else None
        }
    
    async def close(self):
        """Close the aiohttp session."""
        if self._session and not self._session.closed:
            await self._session.close()

