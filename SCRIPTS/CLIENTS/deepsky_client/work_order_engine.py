"""
Work Order Engine — Auto-detects issues and generates structured work orders.

Part of DeepSky Self-Healing AI Client (YuniScript).
Spec: DEEPSKY_SELF_HEALING_ECOSYSTEM_SPEC.md (in YuniScripts base)

Flow:
1. Error event received (from code exception, data flow anomaly, timeout)
2. Error is categorized (code_bug, api_error, data_flow, timeout, resource)
3. Full context is captured (stack trace, data flow path, session state, timestamps)
4. Structured error report is generated
5. Work order created in Documentation.db via create_work_order
6. Error snapshot stored for system prompt generation
7. Session state checkpointed for recovery
"""

import json
import logging
import os
import sys
import time
import traceback
import hashlib
from enum import Enum, auto
from typing import Optional, Dict, Any, List, Tuple
from session_manager import FlushReason

logger = logging.getLogger(__name__)


class ErrorCategory(Enum):
    """Category of error detected."""
    CODE_BUG = 'code_bug'
    API_ERROR = 'api_error'
    DATA_FLOW = 'data_flow'
    TIMEOUT = 'timeout'
    RESOURCE = 'resource'
    CONFIGURATION = 'configuration'
    UNKNOWN = 'unknown'


class ErrorSeverity(Enum):
    """Severity determines work order priority and response."""
    CRITICAL = 1
    ERROR = 2
    WARNING = 3
    INFO = 4


class ErrorReport:
    """Structured error report for work order generation."""
    
    def __init__(self, 
                 error_id: str,
                 category: ErrorCategory,
                 severity: ErrorSeverity,
                 summary: str,
                 details: str,
                 component: str,
                 stack_trace: Optional[str] = None,
                 data_flow_path: Optional[List[str]] = None,
                 context: Optional[Dict] = None,
                 session_summary: Optional[Dict] = None,
                 timestamp: Optional[float] = None):
        self.error_id = error_id
        self.category = category
        self.severity = severity
        self.summary = summary
        self.details = details
        self.component = component
        self.stack_trace = stack_trace
        self.data_flow_path = data_flow_path or []
        self.context = context or {}
        self.session_summary = session_summary or {}
        self.timestamp = timestamp or time.time()
        self.work_order_id = None
    
    def to_dict(self) -> Dict:
        return {
            'error_id': self.error_id,
            'category': self.category.value,
            'severity': self.severity.value,
            'summary': self.summary,
            'details': self.details,
            'component': self.component,
            'stack_trace': self.stack_trace,
            'data_flow_path': self.data_flow_path,
            'context': self.context,
            'session_summary': self.session_summary,
            'timestamp': self.timestamp,
            'work_order_id': self.work_order_id
        }


class WorkOrderEngine:
    """
    Central engine for detecting issues and generating work orders.
    
    Connects to Phooks for error events and can also be called directly.
    Creates structured error reports and work orders for the healing agent.
    """
    
    def __init__(self, config: Dict[str, Any], session_manager=None):
        self.config = config
        self.session_manager = session_manager
        self.phooks_client = None
        
        # Error dedup tracking
        self._recent_errors = {}  # error_hash -> (count, first_seen)
        self._dedup_window = 300  # 5 minutes
        
        # Work order creation via database
        from ecosystem_config import get_documentation_db_path
        self._db_path = get_documentation_db_path()
        
        # Status
        self._enabled = config.get('enabled', True)
        self._error_count = 0
        self._error_id_counter = 0
    
    def set_phooks_client(self, phooks_client):
        """Connect to Phooks for event-driven error detection."""
        self.phooks_client = phooks_client
        logger.info("Phooks client connected to WorkOrderEngine")
    
    # ─── Error Event Handlers ────────────────────────────────────
    
    async def on_error_event(self, error_data: Dict) -> Optional[int]:
        """
        Handle an error event. Creates work order if appropriate.
        
        Args:
            error_data: Dict with at minimum:
                - 'error': Exception or error string
                - 'component': Component name
                Optional:
                - 'stack_trace': Stack trace string
                - 'data_flow_path': List of data flow steps
                - 'context': Additional context dict
                - 'severity': 'critical', 'error', 'warning', 'info'
                
        Returns:
            Work order ID if created, None if suppressed
        """
        if not self._enabled:
        
            return None
        
        
        # Count all error events (including duplicates)
        
        self._error_count += 1
        
        # Extract and categorize
        error_obj = error_data.get('error', 'Unknown error')
        error_str = str(error_obj)
        
        component = error_data.get('component', 'unknown')

        # -- Auto-Healing Noise Filter ---------------------------
        # Reject errors with insufficient context to prevent noise WOs
        # that waste healing agent cycles. See WO #209.
        MEANINGLESS_COMPONENTS = {'unknown', 'c', '', 'none', 'general', 'default', 'component'}
        MIN_CONTEXT_LENGTH = 3

        is_meaningless_component = component.lower().strip() in MEANINGLESS_COMPONENTS
        is_short_error = len(error_str.strip()) < MIN_CONTEXT_LENGTH
        has_stack_trace = bool(error_data.get('stack_trace'))
        has_data_flow = bool(error_data.get('data_flow_path'))
        has_context = bool(error_data.get('context'))

        context_score = sum([
            not is_short_error,
            not is_meaningless_component,
            has_stack_trace,
            has_data_flow,
            has_context,
        ])

        if context_score < 2:
            msg = f'Insufficient context (score={context_score}, need>=2): error={repr(error_str[:80])}, component={repr(component)}'
            self._log_rejected_event(error_data, msg)
            return None
        
        # Dedup check
        error_hash = self._compute_error_hash(error_str, component)
        if self._is_duplicate(error_hash):
            return None
        
        # Categorize
        category = self._categorize_error(error_obj, error_data)
        severity = self._determine_severity(error_data.get('severity'), category)
        
        # Capture context
        stack_trace = error_data.get('stack_trace') or self._capture_stack_trace()
        data_flow_path = error_data.get('data_flow_path', [])
        context = error_data.get('context', {})
        session_summary = self._get_session_summary()
        
        # Generate error ID
        error_id = self._generate_error_id()
        
        # Create report
        report = ErrorReport(
            error_id=error_id,
            category=category,
            severity=severity,
            summary=f"[{category.value.upper()}] {error_str[:200]}",
            details=self._format_details(error_str, component, context),
            component=component,
            stack_trace=stack_trace,
            data_flow_path=data_flow_path,
            context=context,
            session_summary=session_summary
        )
        
        # Checkpoint session state if session manager available
        if self.session_manager:
            self.session_manager.flush(FlushReason.BUG_DETECTED)
        
        # Create work order
        work_order_id = self._create_work_order(report)
        report.work_order_id = work_order_id
        
        # Log
        logger.info(f"Error report created: {error_id} -> work order #{work_order_id} "
                   f"({category.value}, severity={severity.value})")
        
        return work_order_id
    
    async def on_data_flow_error(self, flow_path: List[str], context: Dict) -> Optional[int]:
        """
        Handle a data flow anomaly detection.
        
        Args:
            flow_path: List of data flow steps
            context: Dict with expected/actual data descriptions
            
        Returns:
            Work order ID if created
        """
        return await self.on_error_event({
            'error': f"Data flow anomaly detected at step: {flow_path[-1] if flow_path else 'unknown'}",
            'component': 'data_flow',
            'stack_trace': None,
            'data_flow_path': flow_path,
            'context': context,
            'severity': 'error'
        })
    
    async def on_timeout(self, component: str, timeout_duration: float) -> Optional[int]:
        """
        Handle a timeout event.
        
        Args:
            component: Component that timed out
            timeout_duration: Timeout duration in seconds
            
        Returns:
            Work order ID if created
        """
        return await self.on_error_event({
            'error': f"Timeout in {component}: exceeded {timeout_duration}s",
            'component': component,
            'severity': 'warning' if timeout_duration < 10 else 'error',
            'context': {'timeout_duration': timeout_duration}
        })
    
    # ─── Error Categorization ────────────────────────────────────
    
    def _categorize_error(self, error: Any, error_data: Dict) -> ErrorCategory:
        """Categorize an error based on content."""
        error_str = str(error).lower()
        error_type = type(error).__name__
        
        # Explicit override
        if 'category' in error_data:
            try:
                return ErrorCategory(error_data['category'])
            except ValueError:
                pass
        
        # API errors
        if any(kw in error_str for kw in ['api key', 'authentication', '401', '403', 
                                           'rate limit', '429', 'deepseek', 'httperror',
                                           'http error', 'http response']):
            return ErrorCategory.API_ERROR
        
        if any(kw in error_type.lower() for kw in ['apierror', 'http', 'request',
                                                     'httperror']):
            return ErrorCategory.API_ERROR
        
        # Timeout
        if any(kw in error_type.lower() for kw in ['timeout', 'timeouterror']):
            return ErrorCategory.TIMEOUT
        if 'timeout' in error_str:
            return ErrorCategory.TIMEOUT
        
        # Data flow
        if any(kw in error_str for kw in ['keyerror', 'attributeerror', 'typeerror',
                                           'valueerror', 'indexerror']):
            return ErrorCategory.DATA_FLOW
        if any(kw in error_type for kw in ['KeyError', 'AttributeError', 'TypeError',
                                           'ValueError', 'IndexError']):
            return ErrorCategory.DATA_FLOW
        
        # Resource
        if any(kw in error_str for kw in ['memory', 'disk', 'oom', 'nomemory',
                                           'disk full', 'connection refused']):
            return ErrorCategory.RESOURCE
        
        if any(kw in error_type for kw in ['MemoryError', 'OSError']):
            return ErrorCategory.RESOURCE
        
        # Configuration
        if any(kw in error_str for kw in ['config', 'configuration', 'setting',
                                           'missing key', 'invalid config']):
            return ErrorCategory.CONFIGURATION
        
        # Default to code bug
        return ErrorCategory.CODE_BUG
    
    def _determine_severity(self, severity_str: Optional[str], 
                          category: ErrorCategory) -> ErrorSeverity:
        """Determine error severity level."""
        if severity_str:
            try:
                return ErrorSeverity[severity_str.upper()]
            except (KeyError, AttributeError):
                pass
        
        # Map categories to default severities
        severity_map = {
            ErrorCategory.CODE_BUG: ErrorSeverity.ERROR,
            ErrorCategory.API_ERROR: ErrorSeverity.ERROR,
            ErrorCategory.DATA_FLOW: ErrorSeverity.ERROR,
            ErrorCategory.TIMEOUT: ErrorSeverity.WARNING,
            ErrorCategory.RESOURCE: ErrorSeverity.CRITICAL,
            ErrorCategory.CONFIGURATION: ErrorSeverity.WARNING,
            ErrorCategory.UNKNOWN: ErrorSeverity.WARNING
        }
        return severity_map.get(category, ErrorSeverity.WARNING)
    
    # ─── Context Capture ─────────────────────────────────────────
    
    def _capture_stack_trace(self) -> str:
        """Capture current stack trace."""
        return ''.join(traceback.format_exception(*sys.exc_info())) if sys.exc_info()[0] else None
    
    def _get_session_summary(self) -> Dict:
        """Get session summary from session manager."""
        if self.session_manager:
            return self.session_manager.get_state_summary()
        return {}
    
    def _format_details(self, error_str: str, component: str, context: Dict) -> str:
        """Format detailed error description."""
        parts = [f"Component: {component}", f"Error: {error_str}"]
        
        if context:
            parts.append("Context:")
            for key, value in context.items():
                parts.append(f"  {key}: {value}")
        
        return '\n'.join(parts)
    
    # ─── Deduplication ───────────────────────────────────────────
    
    def _compute_error_hash(self, error_str: str, component: str) -> str:
        """Compute a hash for error deduplication."""
        raw = f"{component}:{error_str[:100]}"
        return hashlib.md5(raw.encode('utf-8')).hexdigest()
    
    def _is_duplicate(self, error_hash: str) -> bool:
        """Check if this error was recently seen."""
        now = time.time()
        
        # Clean old entries
        self._recent_errors = {
            h: (c, t) for h, (c, t) in self._recent_errors.items()
            if (now - t) < self._dedup_window
        }
        
        if error_hash in self._recent_errors:
            count, first_seen = self._recent_errors[error_hash]
            self._recent_errors[error_hash] = (count + 1, first_seen)
            logger.debug(f"Duplicate error suppressed (count: {count + 1})")
            return True
        
        self._recent_errors[error_hash] = (1, now)
        return False
    
    def _generate_error_id(self) -> str:
        """Generate a unique error ID."""
        self._error_id_counter += 1
        timestamp = int(time.time())
        return f"ERR-{timestamp}-{self._error_id_counter:04d}"
    
    # ─── Work Order Creation ─────────────────────────────────────
    
    def _ensure_noise_log_table(self):
        import sqlite3
        conn = sqlite3.connect(self._db_path)
        sql = (
            "CREATE TABLE IF NOT EXISTS noise_log ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "timestamp TEXT DEFAULT (datetime('now')), "
            "error_id TEXT, component TEXT, reason TEXT, "
            "error_data_json TEXT)"
        )
        conn.execute(sql)
        conn.commit()
        conn.close()
    def _log_rejected_event(self, error_data, reason):
        """Log a rejected error event (noise) to the database."""
        self._ensure_noise_log_table()
        import sqlite3, json
        eid = f'REJ-{int(__import__("time").time())}-{self._error_id_counter:04d}'
        self._error_id_counter += 1
        conn = sqlite3.connect(self._db_path)
        conn.execute(
            'INSERT INTO noise_log '
            '(error_id, component, reason, error_data_json) '
            'VALUES (?, ?, ?, ?)',
            (eid, error_data.get('component', 'unknown'),
             reason[:500], json.dumps(error_data, default=str)[:2000])
        )
        conn.commit()
        conn.close()
        logger.debug(f'REJECTED noise event: {reason}')

    def _create_work_order(self, report: ErrorReport) -> int:

        """
        Create a work order in the Documentation.db.
        
        Uses the create_work_order function directly.
        """
        # Map severity to priority
        priority_map = {
            ErrorSeverity.CRITICAL: 1,
            ErrorSeverity.ERROR: 2,
            ErrorSeverity.WARNING: 3,
            ErrorSeverity.INFO: 5
        }
        priority = priority_map.get(report.severity, 3)
        
        # Build description
        description = (
            f"[AUTO-HEALING] {report.summary}\n"
            f"Category: {report.category.value}\n"
            f"Component: {report.component}\n"
            f"Error ID: {report.error_id}\n"
            f"Timestamp: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(report.timestamp))}"
        )
        
        # Build notes with full context
        notes_parts = [
            f"## Auto-Generated Error Report",
            f"**Error ID:** {report.error_id}",
            f"**Category:** {report.category.value}",
            f"**Severity:** {report.severity.value}",
            f"**Component:** {report.component}",
            f"**Summary:** {report.summary}",
            f"**Details:**",
            report.details,
        ]
        
        if report.stack_trace:
            notes_parts.extend([
                f"\n**Stack Trace:**",
                f"```",
                report.stack_trace[:2000],
                f"```"
            ])
        
        if report.data_flow_path:
            notes_parts.extend([
                f"\n**Data Flow Path:**",
                ' -> '.join(report.data_flow_path)
            ])
        
        if report.session_summary:
            notes_parts.extend([
                f"\n**Session State:**",
                f"```json",
                json.dumps(report.session_summary, indent=2),
                f"```"
            ])
        
        notes = '\n'.join(notes_parts)
        
                # Create the work order (this calls the DB directly)
        import sqlite3
        try:
            conn = sqlite3.connect(self._db_path)
            cursor = conn.cursor()
            
            cursor.execute('''
                INSERT INTO work_orders 
                (status, priority, description, notes, assigned_to, created_at, updated_at)
                VALUES ('pending', ?, ?, ?, 'AI', datetime('now'), datetime('now'))
            ''', (priority, description, notes))
            
            work_order_id = cursor.lastrowid
            conn.commit()
            conn.close()
            
            # Also set deepsky_work_mode
            conn = sqlite3.connect(self._db_path)
            cursor = conn.cursor()
            cursor.execute('''
                INSERT OR REPLACE INTO config (key, value, description)
                VALUES ('deepsky_work_mode', 'auto', 'Auto-set by healing system')
            ''')
            conn.commit()
            conn.close()
            return work_order_id
        except Exception as e:
            logger.error(f"Failed to create work order: {e}")
            return -1
# ─── Control ─────────────────────────────────────────────────
    
    def enable(self):
        """Enable the work order engine."""
        self._enabled = True
        logger.info("WorkOrderEngine enabled")
    
    def disable(self):
        """Disable the work order engine."""
        self._enabled = False
        logger.info("WorkOrderEngine disabled")
    
    def get_stats(self) -> Dict:
        """Get engine statistics."""
        return {
            'enabled': self._enabled,
            'error_count': self._error_count,
            'recent_errors': len(self._recent_errors),
            'dedup_window': self._dedup_window
        }

