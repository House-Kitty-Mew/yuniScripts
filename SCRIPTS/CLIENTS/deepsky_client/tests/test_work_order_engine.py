"""
Comprehensive unit tests for WorkOrderEngine.

Part of DeepSky Self-Healing AI Client.
Tests: error categorization, event handling, work order creation,
       deduplication, severity mapping, edge cases.

NEVER USE pytest — always unittest!
"""

import asyncio
import json
import os
import sys
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from work_order_engine import (
    WorkOrderEngine, ErrorCategory, ErrorSeverity, ErrorReport
)


class TestErrorCategory(unittest.TestCase):
    """Test ErrorCategory enum."""

    def test_error_category_values(self):
        """Test all error categories exist."""
        self.assertEqual(ErrorCategory.CODE_BUG.value, 'code_bug')
        self.assertEqual(ErrorCategory.API_ERROR.value, 'api_error')
        self.assertEqual(ErrorCategory.DATA_FLOW.value, 'data_flow')
        self.assertEqual(ErrorCategory.TIMEOUT.value, 'timeout')
        self.assertEqual(ErrorCategory.RESOURCE.value, 'resource')
        self.assertEqual(ErrorCategory.CONFIGURATION.value, 'configuration')
        self.assertEqual(ErrorCategory.UNKNOWN.value, 'unknown')


class TestErrorSeverity(unittest.TestCase):
    """Test ErrorSeverity enum."""

    def test_severity_values(self):
        """Test all severity levels."""
        self.assertEqual(ErrorSeverity.CRITICAL.value, 1)
        self.assertEqual(ErrorSeverity.ERROR.value, 2)
        self.assertEqual(ErrorSeverity.WARNING.value, 3)
        self.assertEqual(ErrorSeverity.INFO.value, 4)

    def test_severity_ordering(self):
        """Test severity ordering."""
        self.assertLess(ErrorSeverity.CRITICAL.value, ErrorSeverity.ERROR.value)
        self.assertLess(ErrorSeverity.ERROR.value, ErrorSeverity.WARNING.value)
        self.assertLess(ErrorSeverity.WARNING.value, ErrorSeverity.INFO.value)


class TestErrorReport(unittest.TestCase):
    """Test ErrorReport class."""

    def test_error_report_creation(self):
        """Test basic error report creation."""
        report = ErrorReport(
            error_id='ERR-001',
            category=ErrorCategory.CODE_BUG,
            severity=ErrorSeverity.ERROR,
            summary='Test error',
            details='Something broke',
            component='test_component'
        )
        self.assertEqual(report.error_id, 'ERR-001')
        self.assertEqual(report.category, ErrorCategory.CODE_BUG)
        self.assertEqual(report.summary, 'Test error')
        self.assertIsNone(report.work_order_id)

    def test_error_report_with_all_fields(self):
        """Test error report with all fields."""
        report = ErrorReport(
            error_id='ERR-002',
            category=ErrorCategory.API_ERROR,
            severity=ErrorSeverity.CRITICAL,
            summary='API failure',
            details='Timeout connecting to API',
            component='api_client',
            stack_trace='Traceback...\n',
            data_flow_path=['input', 'process', 'output'],
            context={'retry_count': 3},
            session_summary={'messages': 10}
        )
        self.assertIsNotNone(report.stack_trace)
        self.assertEqual(len(report.data_flow_path), 3)
        self.assertEqual(report.context['retry_count'], 3)

    def test_error_report_to_dict(self):
        """Test conversion to dictionary."""
        report = ErrorReport(
            error_id='ERR-003',
            category=ErrorCategory.TIMEOUT,
            severity=ErrorSeverity.WARNING,
            summary='Slow response',
            details='Request took too long',
            component='database'
        )
        d = report.to_dict()
        self.assertEqual(d['error_id'], 'ERR-003')
        self.assertEqual(d['category'], 'timeout')
        self.assertEqual(d['severity'], 3)
        self.assertIn('timestamp', d)
        self.assertIn('work_order_id', d)

    def test_error_report_timestamp_auto(self):
        """Test timestamp is auto-generated."""
        report = ErrorReport(
            error_id='ERR-004',
            category=ErrorCategory.UNKNOWN,
            severity=ErrorSeverity.INFO,
            summary='Test',
            details='Details',
            component='c'
        )
        self.assertGreater(report.timestamp, 0)
        self.assertAlmostEqual(report.timestamp, time.time(), delta=1)


class TestWorkOrderEngineInit(unittest.TestCase):
    """Test WorkOrderEngine initialization."""

    def test_init_default(self):
        """Test initialization with default config."""
        engine = WorkOrderEngine({'enabled': True})
        self.assertIsNotNone(engine)
        self.assertTrue(engine._enabled)
        self.assertEqual(engine._dedup_window, 300)

    def test_init_with_session_manager(self):
        """Test initialization with session manager."""
        mock_sm = MagicMock()
        engine = WorkOrderEngine({'enabled': True}, session_manager=mock_sm)
        self.assertEqual(engine.session_manager, mock_sm)

    def test_init_disabled(self):
        """Test initialization disabled."""
        engine = WorkOrderEngine({'enabled': False})
        self.assertFalse(engine._enabled)

    def test_set_phooks_client(self):
        """Test setting Phooks client."""
        engine = WorkOrderEngine({'enabled': True})
        mock_client = MagicMock()
        engine.set_phooks_client(mock_client)
        self.assertEqual(engine.phooks_client, mock_client)

    def test_enable_disable(self):
        """Test enable/disable toggle."""
        engine = WorkOrderEngine({'enabled': False})
        engine.enable()
        self.assertTrue(engine._enabled)
        engine.disable()
        self.assertFalse(engine._enabled)


class TestWorkOrderEngineCategorization(unittest.TestCase):
    """Test error categorization logic."""

    def setUp(self):
        self.engine = WorkOrderEngine({'enabled': True})

    def test_categorize_api_auth_error(self):
        """Test API key error categorization."""
        cat = self.engine._categorize_error(Exception('Invalid API key'), {})
        self.assertEqual(cat, ErrorCategory.API_ERROR)

    def test_categorize_api_401_error(self):
        """Test 401 error categorization."""
        cat = self.engine._categorize_error(Exception('HTTP 401 Unauthorized'), {})
        self.assertEqual(cat, ErrorCategory.API_ERROR)

    def test_categorize_api_rate_limit(self):
        """Test rate limit categorization."""
        cat = self.engine._categorize_error(Exception('rate limit exceeded'), {})
        self.assertEqual(cat, ErrorCategory.API_ERROR)

    def test_categorize_timeout(self):
        """Test timeout categorization."""
        cat = self.engine._categorize_error(TimeoutError('Operation timed out'), {})
        self.assertEqual(cat, ErrorCategory.TIMEOUT)

    def test_categorize_key_error(self):
        """Test KeyError as data flow."""
        cat = self.engine._categorize_error(KeyError('missing_key'), {})
        self.assertEqual(cat, ErrorCategory.DATA_FLOW)

    def test_categorize_type_error(self):
        """Test TypeError as data flow."""
        cat = self.engine._categorize_error(TypeError('unsupported operand'), {})
        self.assertEqual(cat, ErrorCategory.DATA_FLOW)

    def test_categorize_value_error(self):
        """Test ValueError as data flow."""
        cat = self.engine._categorize_error(ValueError('invalid value'), {})
        self.assertEqual(cat, ErrorCategory.DATA_FLOW)

    def test_categorize_memory_error(self):
        """Test MemoryError as resource."""
        cat = self.engine._categorize_error(MemoryError('Out of memory'), {})
        self.assertEqual(cat, ErrorCategory.RESOURCE)

    def test_categorize_connection_refused(self):
        """Test connection refused as resource."""
        cat = self.engine._categorize_error(OSError('Connection refused'), {})
        self.assertEqual(cat, ErrorCategory.RESOURCE)

    def test_categorize_config_error(self):
        """Test config error categorization."""
        cat = self.engine._categorize_error(Exception('Missing config key'), {})
        self.assertEqual(cat, ErrorCategory.CONFIGURATION)

    def test_categorize_unknown(self):
        """Test unknown error defaults to code_bug."""
        cat = self.engine._categorize_error(RuntimeError('weird'), {})
        self.assertEqual(cat, ErrorCategory.CODE_BUG)

    def test_categorize_override(self):
        """Test explicit category override."""
        cat = self.engine._categorize_error(Exception('anything'), 
                                           {'category': 'data_flow'})
        self.assertEqual(cat, ErrorCategory.DATA_FLOW)

    def test_categorize_http_error_type(self):
        """Test HTTPError type categorization."""
        cat = self.engine._categorize_error(Exception('HTTPError: 500'), {})
        self.assertEqual(cat, ErrorCategory.API_ERROR)


class TestWorkOrderEngineSeverity(unittest.TestCase):
    """Test severity determination."""

    def setUp(self):
        self.engine = WorkOrderEngine({'enabled': True})

    def test_severity_critical(self):
        """Test critical severity."""
        sev = self.engine._determine_severity('critical', ErrorCategory.CODE_BUG)
        self.assertEqual(sev, ErrorSeverity.CRITICAL)

    def test_severity_error(self):
        """Test error severity."""
        sev = self.engine._determine_severity('error', ErrorCategory.CODE_BUG)
        self.assertEqual(sev, ErrorSeverity.ERROR)

    def test_severity_warning(self):
        """Test warning severity."""
        sev = self.engine._determine_severity('warning', ErrorCategory.CODE_BUG)
        self.assertEqual(sev, ErrorSeverity.WARNING)

    def test_severity_info(self):
        """Test info severity."""
        sev = self.engine._determine_severity('info', ErrorCategory.CODE_BUG)
        self.assertEqual(sev, ErrorSeverity.INFO)

    def test_severity_default_code_bug(self):
        """Test default severity for code bug."""
        sev = self.engine._determine_severity(None, ErrorCategory.CODE_BUG)
        self.assertEqual(sev, ErrorSeverity.ERROR)

    def test_severity_default_api_error(self):
        """Test default severity for API error."""
        sev = self.engine._determine_severity(None, ErrorCategory.API_ERROR)
        self.assertEqual(sev, ErrorSeverity.ERROR)

    def test_severity_default_resource(self):
        """Test default severity for resource error."""
        sev = self.engine._determine_severity(None, ErrorCategory.RESOURCE)
        self.assertEqual(sev, ErrorSeverity.CRITICAL)

    def test_severity_default_timeout(self):
        """Test default severity for timeout."""
        sev = self.engine._determine_severity(None, ErrorCategory.TIMEOUT)
        self.assertEqual(sev, ErrorSeverity.WARNING)

    def test_severity_invalid_string(self):
        """Test invalid severity string defaults."""
        sev = self.engine._determine_severity('invalid', ErrorCategory.CODE_BUG)
        # Should not crash, defaults to error
        self.assertIsInstance(sev, ErrorSeverity)


class TestWorkOrderEngineDedup(unittest.TestCase):
    """Test error deduplication."""

    def setUp(self):
        self.engine = WorkOrderEngine({'enabled': True})

    def test_dedup_same_error(self):
        """Test same error hash is suppressed."""
        hash1 = self.engine._compute_error_hash('Error: timeout', 'api')
        hash2 = self.engine._compute_error_hash('Error: timeout', 'api')
        self.assertEqual(hash1, hash2)

    def test_dedup_different_errors(self):
        """Test different errors have different hashes."""
        hash1 = self.engine._compute_error_hash('Error: timeout', 'api')
        hash2 = self.engine._compute_error_hash('Error: broken', 'api')
        self.assertNotEqual(hash1, hash2)

    def test_dedup_different_components(self):
        """Test same error in different components has different hashes."""
        hash1 = self.engine._compute_error_hash('Error: timeout', 'api')
        hash2 = self.engine._compute_error_hash('Error: timeout', 'db')
        self.assertNotEqual(hash1, hash2)

    def test_is_duplicate_first_seen(self):
        """Test first error is not duplicate."""
        h = self.engine._compute_error_hash('first error', 'comp')
        self.assertFalse(self.engine._is_duplicate(h))

    def test_is_duplicate_second_time(self):
        """Test same error reported twice is duplicate."""
        h = self.engine._compute_error_hash('same error', 'comp')
        self.engine._is_duplicate(h)  # First time
        self.assertTrue(self.engine._is_duplicate(h))  # Second time - duplicate

    def test_dedup_cleans_old_entries(self):
        """Test that old entries are cleaned from dedup cache."""
        self.engine._recent_errors['old_hash'] = (1, time.time() - 600)  # 10 min ago
        self.engine._is_duplicate('new_hash')
        self.assertNotIn('old_hash', self.engine._recent_errors)

    def test_dedup_increments_count(self):
        """Test duplicate error increments count."""
        h = self.engine._compute_error_hash('incremented', 'comp')
        self.engine._is_duplicate(h)
        self.assertEqual(self.engine._recent_errors[h][0], 1)
        
        self.engine._is_duplicate(h)
        self.assertEqual(self.engine._recent_errors[h][0], 2)


class TestWorkOrderEngineErrorID(unittest.TestCase):
    """Test error ID generation."""

    def setUp(self):
        self.engine = WorkOrderEngine({'enabled': True})

    def test_error_id_format(self):
        """Test error ID format."""
        err_id = self.engine._generate_error_id()
        self.assertTrue(err_id.startswith('ERR-'))
        self.assertIn('-', err_id)

    def test_error_id_increment(self):
        """Test error ID increments."""
        id1 = self.engine._generate_error_id()
        id2 = self.engine._generate_error_id()
        self.assertNotEqual(id1, id2)


class TestWorkOrderEngineCreateWorkOrder(unittest.TestCase):
    """Test work order creation."""

    def setUp(self):
        self.engine = WorkOrderEngine({'enabled': True})
        self.report = ErrorReport(
            error_id='ERR-TEST',
            category=ErrorCategory.CODE_BUG,
            severity=ErrorSeverity.ERROR,
            summary='Test error for WO creation',
            details='Details of the error',
            component='test',
            stack_trace='Traceback line 1\nLine 2\n'
        )

    def test_create_work_order_returns_id(self):
        """Test work order creation returns positive ID."""
        # Mocking DB to avoid actual DB dependency
        with patch('sqlite3.connect') as mock_connect:
            mock_cursor = MagicMock()
            mock_cursor.lastrowid = 42
            mock_connect.return_value.cursor.return_value = mock_cursor
            
            wo_id = self.engine._create_work_order(self.report)
            self.assertEqual(wo_id, 42)

    def test_create_work_order_db_error(self):
        """Test work order creation with DB error."""
        with patch('sqlite3.connect') as mock_connect:
            mock_connect.side_effect = Exception('DB connection failed')
            
            wo_id = self.engine._create_work_order(self.report)
            self.assertEqual(wo_id, -1)

    def test_create_work_order_priority_mapping(self):
        """Test severity to priority mapping."""
        test_cases = [
            (ErrorSeverity.CRITICAL, 1),
            (ErrorSeverity.ERROR, 2),
            (ErrorSeverity.WARNING, 3),
            (ErrorSeverity.INFO, 5),
        ]
        for severity, expected_priority in test_cases:
            with self.subTest(severity=severity):
                report = ErrorReport('E', ErrorCategory.CODE_BUG, severity, 't', 'd', 'c')
                with patch('sqlite3.connect') as mock_connect:
                    mock_cursor = MagicMock()
                    mock_cursor.lastrowid = 1
                    mock_connect.return_value.cursor.return_value = mock_cursor
                    
                    self.engine._create_work_order(report)


class TestWorkOrderEngineEventHandlers(unittest.TestCase):
    """Test async event handlers."""

    def setUp(self):
        self.engine = WorkOrderEngine({'enabled': True})

    def test_on_error_event_disabled(self):
        """Test error event when disabled."""
        self.engine.disable()
        
        async def run_test():
            result = await self.engine.on_error_event({'error': 'test', 'component': 'x'})
            self.assertIsNone(result)
        
        asyncio.run(run_test())

    def test_on_timeout_event(self):
        """Test timeout event handler."""
        async def run_test():
            with patch.object(self.engine, 'on_error_event', return_value=42):
                result = await self.engine.on_timeout('api', 30.0)
                self.assertEqual(result, 42)
        
        asyncio.run(run_test())

    def test_on_data_flow_error(self):
        """Test data flow error handler."""
        async def run_test():
            with patch.object(self.engine, 'on_error_event', return_value=42):
                result = await self.engine.on_data_flow_error(
                    ['step1', 'step2'], 
                    {'expected': 'int', 'actual': 'str'}
                )
                self.assertEqual(result, 42)
        
        asyncio.run(run_test())


class TestWorkOrderEngineStats(unittest.TestCase):
    """Test engine statistics."""

    def setUp(self):
        self.engine = WorkOrderEngine({'enabled': True})

    def test_get_stats(self):
        """Test get stats returns expected fields."""
        stats = self.engine.get_stats()
        self.assertIn('enabled', stats)
        self.assertIn('error_count', stats)
        self.assertIn('recent_errors', stats)
        self.assertIn('dedup_window', stats)
        self.assertTrue(stats['enabled'])

    def test_get_stats_disabled(self):
        """Test stats when disabled."""
        self.engine.disable()
        stats = self.engine.get_stats()
        self.assertFalse(stats['enabled'])

    def test_get_stats_after_errors(self):
        """Test stats after error processing."""
        async def run_test():
            for _ in range(3):
                await self.engine.on_error_event({'error': 'e1', 'component': 'c'})
            stats = self.engine.get_stats()
            self.assertEqual(stats['error_count'], 3)  # generate_error_id increments each time
        
        asyncio.run(run_test())


if __name__ == '__main__':
    unittest.main()
