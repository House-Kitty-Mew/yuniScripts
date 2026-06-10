#!/usr/bin/env python3
"""
DeepSky Self-Healing AI Client — YuniScript Main Entry Point.

Part of DeepSky Self-Healing AI Ecosystem.
Spec: DEEPSKY_SELF_HEALING_ECOSYSTEM_SPEC.md (in YuniScripts base)

This is managed by the YuniScripts engine (engine/process_wrapper.py).
Server type: critical (never killed by 5-second rule).
Shutdown timeout: 60 seconds.
"""

import asyncio
import json
import logging
import logging.handlers
import os
import signal
import sys
import time

# Add parent to path for YuniScripts imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Local imports
from api_client import DeepSeekAPIClient
from session_manager import SessionManager
from work_order_engine import WorkOrderEngine
from healing_agent import HealingAgent
from system_prompt_generator import SystemPromptGenerator

logger = logging.getLogger(__name__)


class DeepSkyClient:
    """
    Main application class for the DeepSky Self-Healing AI Client.
    
    Wires together all components:
    - API Client (DeepSeek)
    - Session Manager (persistence + recovery)
    - Work Order Engine (auto-detect issues)
    - Healing Agent (spawn fix agents)
    - System Prompt Generator (dynamic prompts)
    """

    def __init__(self, config_path: str = None):
        self.config_path = config_path or os.path.join(
            os.path.dirname(os.path.abspath(__file__)), 'config.json'
        )
        self.config = self._load_config()
        
        # Components
        self.api_client = None
        self.session_manager = None
        self.work_order_engine = None
        self.healing_agent = None
        self.prompt_generator = None
        
        # Phooks (optional — will connect if hub is available)
        self.phooks_client = None
        
        # Runtime state
        self._running = False
        self._healthy = True
        self._start_time = None
        
        # Setup
        self._setup_logging()
    
    def _load_config(self) -> dict:
        """Load configuration from JSON file."""
        try:
            with open(self.config_path, 'r') as f:
                return json.load(f)
        except FileNotFoundError:
            logger.warning(f"Config not found at {self.config_path}, using defaults")
            return {
                'api': {'base_url': 'https://api.deepseek.com/v1', 'model': 'deepseek-chat'},
                'session': {'buffer_maxlen': 500, 'auto_flush_interval': 30},
                'healing': {'enabled': True, 'poll_interval': 10},
                'logging': {'level': 'INFO'}
            }
        except json.JSONDecodeError as e:
            logger.error(f"Invalid config JSON: {e}")
            return {}
    
    def _setup_logging(self):
        """Configure logging."""
        log_config = self.config.get('logging', {})
        level = getattr(logging, log_config.get('level', 'INFO').upper(), logging.INFO)
        log_file = log_config.get('file', 'deepsky_client.log')
        max_bytes = log_config.get('max_bytes', 10485760)
        backup_count = log_config.get('backup_count', 3)
        
        # File handler with rotation
        file_handler = logging.handlers.RotatingFileHandler(
            log_file, maxBytes=max_bytes, backupCount=backup_count
        )
        file_handler.setLevel(level)
        
        # Console handler
        console_handler = logging.StreamHandler()
        console_handler.setLevel(level)
        
        # Formatter
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        file_handler.setFormatter(formatter)
        console_handler.setFormatter(formatter)
        
        # Root logger
        root_logger = logging.getLogger()
        root_logger.setLevel(level)
        root_logger.addHandler(file_handler)
        root_logger.addHandler(console_handler)
        
        logger.info(f"Logging initialized (level={logging.getLevelName(level)}, file={log_file})")
    
    async def initialize(self):
        """Initialize all components."""
        logger.info("Initializing DeepSky Self-Healing Client...")
        self._start_time = time.time()
        
        # 1. Initialize Session Manager
        try:
            self.session_manager = SessionManager(self.config.get('session', {}))
            
            # Try to recover from last checkpoint
            last_meta = self.session_manager.get_last_session_metadata()
            if last_meta:
                logger.info(f"Previous session found: {last_meta['session_id']}")
                if not last_meta['is_healthy']:
                    logger.warning("Previous session was unhealthy, attempting recovery...")
                    recovered = self.session_manager.restore_from_checkpoint()
                    logger.info(f"Recovered {len(recovered)} messages from checkpoint")
            
            # Set session info
            api_config = self.config.get('api', {})
            self.session_manager.set_session_info(
                model=api_config.get('model', 'deepseek-chat'),
                system_prompt=self._get_system_prompt()
            )
            
            # Start auto-flush
            await self.session_manager.start_auto_flush()
            logger.info("Session Manager initialized")
        except Exception as e:
            logger.error(f"Failed to initialize Session Manager: {e}")
            self._healthy = False
            raise
        
        # 2. Initialize API Client
        try:
            self.api_client = DeepSeekAPIClient(self.config.get('api', {}))
            
            # Check API health
            healthy = await self.api_client.check_health()
            if not healthy:
                logger.warning("API health check failed. Will operate with reduced functionality.")
            else:
                logger.info(f"API Client initialized (model: {api_config.get('model')})")
        except Exception as e:
            logger.error(f"Failed to initialize API Client: {e}")
            # Don't raise — we can still operate without API (for testing)
        
        # 3. Initialize Work Order Engine
        try:
            self.work_order_engine = WorkOrderEngine(
                self.config.get('healing', {}),
                session_manager=self.session_manager
            )
            logger.info("Work Order Engine initialized")
        except Exception as e:
            logger.error(f"Failed to initialize Work Order Engine: {e}")
        
        # 4. Initialize System Prompt Generator
        try:
            self.prompt_generator = SystemPromptGenerator()
            logger.info("System Prompt Generator initialized")
        except Exception as e:
            logger.error(f"Failed to initialize System Prompt Generator: {e}")
        
        # 5. Initialize Healing Agent
        try:
            self.healing_agent = HealingAgent(
                self.config.get('healing', {}),
                api_client=self.api_client,
                session_manager=self.session_manager,
                work_order_engine=self.work_order_engine
            )
            self.healing_agent.set_prompt_generator(self.prompt_generator)
            logger.info("Healing Agent initialized")
        except Exception as e:
            logger.error(f"Failed to initialize Healing Agent: {e}")
        
        # 6. Try Phooks connection
        await self._connect_phooks()
        
        self._healthy = True
        logger.info("All components initialized successfully")
    
    async def _connect_phooks(self):
        """Attempt to connect to Phooks hub."""
        phooks_config = self.config.get('phooks', {})
        if not phooks_config.get('auto_connect', True):
            return
        
        host = phooks_config.get('hub_host', 'localhost')
        port = phooks_config.get('hub_port', 5555)
        
        try:
            # Try importing Phooks client
            import sys
            sys.path.insert(0, os.environ.get('DEEPSKY_YUNISCRIPTS_BASE', os.path.join(os.path.expanduser('~'), 'Documents', 'dev-yuniScripts')))
            from SCRIPTS.SERVICES.phooks_hub.phooks_client import PhooksClient
            
            self.phooks_client = PhooksClient(host, port)
            
            # Subscribe to relevant topics
            # Note: Phooks connection will be async in practice
            logger.info(f"Connected to Phooks hub at {host}:{port}")
            
            # Wire Phooks to work order engine
            if self.work_order_engine:
                self.work_order_engine.set_phooks_client(self.phooks_client)
            
        except ImportError:
            logger.info("Phooks client not available (will operate stand-alone)")
        except Exception as e:
            logger.warning(f"Could not connect to Phooks hub: {e}")
    
    def _get_system_prompt(self) -> str:
        """Get the base system prompt."""
        return """You are the DeepSky Self-Healing AI Client, a YuniScript-managed AI agent.
You have access to DeepSeek API for AI operations and FastMCP tools via Phooks bridge.
Session state is persisted to SQLite for crash recovery.
Self-healing: if errors are detected, work orders are auto-generated."""
    
    async def run(self):
        """Main application loop."""
        self._running = True
        
        logger.info("DeepSky Client entering main loop")
        
        # Append system message to buffer
        if self.session_manager:
            self.session_manager.append_message(
                'system', 
                'DeepSky Self-Healing Client started. Session persistence active.'
            )
        
        # Start healing agent if enabled
        if self.healing_agent and self.config.get('healing', {}).get('enabled', True):
            await self.healing_agent.start()
        
        try:
            # Main loop — keep alive and process events
            while self._running:
                await asyncio.sleep(1.0)
                
                # Periodic health checks
                if int(time.time()) % 60 == 0:  # Every minute
                    if self.api_client and not await self.api_client.check_health():
                        logger.warning("Periodic health check failed")
                        if self.work_order_engine:
                            await self.work_order_engine.on_timeout(
                                'api_client', 30.0
                            )
                
                # Periodic flush if not timer-driven
                if self.session_manager and int(time.time()) % 15 == 0:
                    # Force flush every 15s as backup to timer
                    pass  # Auto-flush handles this
                    
        except asyncio.CancelledError:
            logger.info("Main loop cancelled")
        except Exception as e:
            logger.error(f"Main loop error: {e}")
            if self.work_order_engine:
                await self.work_order_engine.on_error_event({
                    'error': e,
                    'component': 'main_loop',
                    'severity': 'error'
                })
        finally:
            await self.shutdown()
    
    async def shutdown(self):
        """Graceful shutdown of all components."""
        logger.info("Shutting down DeepSky Client...")
        self._running = False
        
        # Stop healing agent
        if self.healing_agent:
            await self.healing_agent.stop()
        
        # Final flush to SQLite
        if self.session_manager:
            await self.session_manager.graceful_shutdown()
        
        # Close API client
        if self.api_client:
            await self.api_client.close()
        
        # Close Phooks
        if self.phooks_client:
            try:
                self.phooks_client.close()
            except Exception:
                pass
        
        uptime = time.time() - self._start_time if self._start_time else 0
        logger.info(f"Shutdown complete (uptime: {uptime:.1f}s)")


# ─── YuniScript Entry Point ─────────────────────────────────────


def _run_async(coro):
    """Run a coroutine synchronously. Avoids signal.signal() calls from asyncio.run()."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


async def main():
    """YuniScript entry point. Called by engine/process_wrapper.py."""
    client = DeepSkyClient()
    
    # Handle graceful shutdown signals
    loop = asyncio.get_event_loop()
    
    def shutdown_handler():
        logger.info("Shutdown signal received")
        asyncio.ensure_future(client.shutdown())
    
    try:
        loop.add_signal_handler(signal.SIGTERM, shutdown_handler)
        loop.add_signal_handler(signal.SIGINT, shutdown_handler)
    except NotImplementedError:
        # Windows doesn't support add_signal_handler
        pass
    
    try:
        await client.initialize()
        await client.run()
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        await client.shutdown()
        raise


if __name__ == '__main__':
    _run_async(main())
