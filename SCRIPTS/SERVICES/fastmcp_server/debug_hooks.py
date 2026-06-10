"""
Debug Hooks — Captures exceptions, data flow errors, and timeouts for work order generation.

Part of FastMCP Server (YuniScript Managed).
Spec: /home/deck/Documents/dev-yuniScripts/DEEPSKY_SELF_HEALING_ECOSYSTEM_SPEC.md

Flow:
1. Exception/error occurs in FastMCP or any managed component
2. DebugHook captures: stack trace, data flow path, context
3. Publishes error event to Phooks 'system.error' topic
4. WorkOrderEngine (in DeepSky client) receives event and creates work order
"""

import asyncio
import json
import logging
import sys
import time
import traceback
import uuid
from typing import Optional, Dict, Any, Callable

logger = logging.getLogger(__name__)


class DebugHooks:
    """
    Captures exceptions and data flow errors for self-healing system.
    
    Publishes error events to Phooks for WorkOrderEngine consumption.
    Also provides decorators for automatic error capture on functions.
    """

    def __init__(self, phooks_client=None):
        self.phooks = phooks_client
        self._error_count = 0
        self._captured_errors = []
        self._max_stored_errors = 100
        self._running = False
        self._hook_task = None

    async def start(self):
        """Start debug hooks."""
        self._running = True
        logger.info("Debug Hooks started")

    async def stop(self):
        """Stop debug hooks."""
        self._running = False
        logger.info("Debug Hooks stopped")

    # ─── Error Capture ──────────────────────────────────────────

    async def on_tool_call_error(self, tool_name: str, error: Exception, 
                                 context: Optional[Dict] = None) -> str:
        """
        Capture a tool call error.
        
        Args:
            tool_name: Name of tool that failed
            error: The exception that was raised
            context: Optional context dict
            
        Returns:
            Error event ID
        """
        event_id = str(uuid.uuid4())
        self._error_count += 1
        
        error_data = {
            'event_id': event_id,
            'type': 'tool_call_error',
            'tool_name': tool_name,
            'error': str(error),
            'error_type': type(error).__name__,
            'stack_trace': ''.join(traceback.format_exception(
                type(error), error, error.__traceback__)),
            'component': f'fastmcp_server.tools.{tool_name}',
            'severity': 'error',
            'context': context or {},
            'timestamp': time.time()
        }
        
        await self._store_and_publish(error_data)
        return event_id

    async def on_data_flow_anomaly(self, flow_path: list, data: Any,
                                   expected: Any, actual: Any,
                                   context: Optional[Dict] = None) -> str:
        """
        Capture a data flow anomaly.
        
        Args:
            flow_path: List of data flow steps
            data: The data that was being processed
            expected: Expected value/type
            actual: Actual value/type found
            context: Optional context dict
            
        Returns:
            Error event ID
        """
        event_id = str(uuid.uuid4())
        self._error_count += 1
        
        error_data = {
            'event_id': event_id,
            'type': 'data_flow_anomaly',
            'flow_path': flow_path,
            'data_summary': str(data)[:500],
            'expected': str(expected)[:200],
            'actual': str(actual)[:200],
            'component': 'fastmcp_server.data_flow',
            'severity': 'error',
            'context': context or {},
            'timestamp': time.time()
        }
        
        await self._store_and_publish(error_data)
        return event_id

    async def on_timeout(self, component: str, timeout_duration: float,
                         context: Optional[Dict] = None) -> str:
        """
        Capture a timeout event.
        
        Args:
            component: Component that timed out
            timeout_duration: Timeout in seconds
            context: Optional context dict
            
        Returns:
            Error event ID
        """
        event_id = str(uuid.uuid4())
        self._error_count += 1
        
        error_data = {
            'event_id': event_id,
            'type': 'timeout',
            'component': component,
            'timeout_duration': timeout_duration,
            'severity': 'warning' if timeout_duration < 30 else 'error',
            'context': context or {},
            'timestamp': time.time()
        }
        
        await self._store_and_publish(error_data)
        return event_id

    # ─── Internal ───────────────────────────────────────────────

    async def _store_and_publish(self, error_data: Dict):
        """Store error locally and publish to Phooks."""
        # Store in local buffer
        self._captured_errors.append(error_data)
        if len(self._captured_errors) > self._max_stored_errors:
            self._captured_errors.pop(0)
        
        # Publish to Phooks
        await self._publish_error(error_data)
        
        # Log
        sev = error_data.get('severity', 'error')
        etype = error_data.get('type', 'unknown')
        logger.log(
            logging.WARNING if sev == 'warning' else logging.ERROR,
            f"[{sev.upper()}] {etype}: {error_data.get('error', error_data.get('data_summary', 'No details'))}"
        )

    async def _publish_error(self, error_data: Dict):
        """Publish error event to Phooks."""
        if not self.phooks:
            return
        
        try:
            if hasattr(self.phooks, 'publish'):
                await self.phooks.publish('system.error', error_data)
                logger.debug(f"Error published to Phooks: {error_data.get('event_id')}")
        except Exception as e:
            logger.error(f"Failed to publish error to Phooks: {e}")

    # ─── Decorator ─────────────────────────────────────────────

    def capture_tool_errors(self, func):
        """
        Decorator that automatically captures exceptions from a tool function.
        
        Usage:
            @debug_hooks.capture_tool_errors
            async def my_tool(param1, param2):
                ...
        """
        async def wrapper(*args, **kwargs):
            try:
                return await func(*args, **kwargs) if asyncio.iscoroutinefunction(func) else func(*args, **kwargs)
            except Exception as e:
                tool_name = getattr(func, '__name__', 'unknown')
                await self.on_tool_call_error(tool_name, e, {
                    'args': str(args)[:200],
                    'kwargs': str(kwargs)[:200]
                })
                raise
        return wrapper

    # ─── Query ──────────────────────────────────────────────────

    def get_recent_errors(self, count: int = 10) -> list:
        """Get the most recent captured errors."""
        return self._captured_errors[-count:] if self._captured_errors else []

    def get_stats(self) -> Dict:
        """Get debug hooks statistics."""
        return {
            'error_count': self._error_count,
            'stored_errors': len(self._captured_errors),
            'max_stored': self._max_stored_errors,
            'running': self._running
        }

    def clear_errors(self):
        """Clear the error buffer."""
        self._captured_errors.clear()
        logger.debug("Error buffer cleared")
