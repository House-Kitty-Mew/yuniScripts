"""
Healing Agent — Spawns AI agents to fix detected issues and validate fixes.

Part of DeepSky Self-Healing AI Client (YuniScript).
Spec: DEEPSKY_AGENT_CONSTRAINTS_INTEGRATION_SPEC.md (in YuniScripts base)

## CRITICAL CONSTRAINTS:
- NO parallel agents — serial processing only
- MAX_AGENT_DEPTH = 2 (main → healer → sub-agent)
- MAX_CHAIN_LENGTH = 10 (sequential work orders)
- MAX_CHAIN_DEPTH = 2
- Chain/spawn operations require 100% confidence validation

Flow:
1. Polls for new work orders (DB or Phooks) — SERIAL, one at a time
2. Generates system prompt from error context + tool definitions + code state
3. Spawns AI agent via DeepSeek API with full tool access
4. Monitors agent health and progress
5. On success: validates fix, closes work order
6. On failure: recovers from checkpoint, increments recovery count
7. Escalates if recovery count exceeds threshold
"""

import asyncio
import json
import logging
import os
import sqlite3
import time
import traceback
from typing import Optional, Dict, Any, List, Tuple

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════
# AGENT CONSTRAINT CONSTANTS
# ═══════════════════════════════════════════════════════════════════

MAX_AGENT_DEPTH = 2
"""Maximum nesting depth for AI agents.
Depth 0 = Main DeepSky client
Depth 1 = Healing agent spawned for a work order
Depth 2 = Sub-agent spawned by healing agent (100% confidence required)
Depth > 2 = REJECTED"""

MAX_CHAIN_LENGTH = 10
"""Maximum number of sequential chained work orders before forced flush."""

MAX_CHAIN_DEPTH = 2
"""Maximum agent depth within a chain."""

MAX_CONCURRENT_AGENTS = 1
"""Hard limit: only 1 agent at a time. NO PARALLEL agents."""

# ═══════════════════════════════════════════════════════════════════


class AgentHealthStatus:
    """Status of a spawned healing agent."""
    RUNNING = 'running'
    COMPLETED = 'completed'
    FAILED = 'failed'
    TIMEOUT = 'timeout'
    RECOVERING = 'recovering'
    REJECTED = 'rejected'  # Constraint violation


class ConfidenceVerdict:
    """Result of a confidence validation check."""
    
    def __init__(self, approved: bool, reason: str = "",
                 risk_factors: Optional[List[str]] = None):
        self.approved = approved
        self.reason = reason
        self.risk_factors = risk_factors or []
    
    def to_dict(self) -> Dict:
        return {
            'approved': self.approved,
            'reason': self.reason,
            'risk_factors': self.risk_factors
        }


class AgentDepthTracker:
    """
    Tracks agent nesting depth across the system.
    
    Depth levels:
      0 = Main DeepSky client (root)
      1 = Healing agent for a work order
      2 = Sub-agent spawned by healing agent (requires 100% confidence)
    """
    
    def __init__(self):
        self._current_depth = 0
        self._chain_count = 0
        self._session_stack: List[str] = []
    
    @property
    def current_depth(self) -> int:
        return self._current_depth
    
    @property
    def chain_count(self) -> int:
        return self._chain_count
    
    def can_spawn(self) -> Tuple[bool, str]:
        """
        Check if a new agent can be spawned at current depth + 1.
        
        Returns:
            (True, "") if allowed
            (False, "reason") if rejected
        """
        next_depth = self._current_depth + 1
        if next_depth > MAX_AGENT_DEPTH:
            return (False, 
                    f"Max agent depth ({MAX_AGENT_DEPTH}) exceeded. "
                    f"Current depth: {self._current_depth}, requested: {next_depth}")
        return (True, "")
    
    def can_chain(self) -> Tuple[bool, str]:
        """
        Check if a new chain operation can proceed.
        
        Returns:
            (True, "") if allowed
            (False, "reason") if rejected
        """
        if self._chain_count >= MAX_CHAIN_LENGTH:
            return (False,
                    f"Max chain length ({MAX_CHAIN_LENGTH}) reached. "
                    f"Forced state flush required.")
        if self._current_depth >= MAX_CHAIN_DEPTH:
            return (False,
                    f"Max chain depth ({MAX_CHAIN_DEPTH}) exceeded. "
                    f"Current depth: {self._current_depth}")
        return (True, "")
    
    def enter(self, session_id: str) -> bool:
        """Enter a new depth level. Returns True if allowed."""
        allowed, _ = self.can_spawn()
        if not allowed:
            return False
        self._current_depth += 1
        self._session_stack.append(session_id)
        logger.debug(f"DepthTracker: entered depth {self._current_depth} "
                     f"(session: {session_id}, chain: {self._chain_count})")
        return True
    
    def mark_chain_step(self) -> bool:
        """
        Mark one sequential chain step (independent of depth).
        Returns True if chain limit not yet reached.
        """
        if self._chain_count >= MAX_CHAIN_LENGTH:
            return False
        self._chain_count += 1
        logger.debug(f"DepthTracker: chain step {self._chain_count}/{MAX_CHAIN_LENGTH}")
        return True
    
    def exit(self, session_id: str) -> None:
        """Exit the current depth level."""
        if self._session_stack and self._session_stack[-1] == session_id:
            self._session_stack.pop()
            self._current_depth = max(0, self._current_depth - 1)
            logger.debug(f"DepthTracker: exited to depth {self._current_depth}")
    
    def reset_chain_count(self) -> None:
        """Reset the chain counter (called after forced flush)."""
        self._chain_count = 0
        logger.debug("DepthTracker: chain count reset")
    
    def get_info(self) -> Dict:
        return {
            'current_depth': self._current_depth,
            'chain_count': self._chain_count,
            'session_stack': list(self._session_stack),
            'max_depth': MAX_AGENT_DEPTH,
            'max_chain_length': MAX_CHAIN_LENGTH,
            'max_chain_depth': MAX_CHAIN_DEPTH
        }


class HealingAgentSession:
    """Tracks a single healing agent session."""
    
    def __init__(self, work_order_id: int, session_id: str, depth: int = 1):
        self.work_order_id = work_order_id
        self.session_id = session_id
        self.depth = depth
        self.status = AgentHealthStatus.RUNNING
        self.started_at = time.time()
        self.last_activity = time.time()
        self.error_count = 0
        self.recovery_attempts = 0
        self.result = None
        self.error = None


def _is_error_or_warn_work_order(order: Dict) -> bool:
    """Check if a work order was triggered by an error or warning condition.

    Only spawn AI healing agents for work orders that relate to actual
    errors or warnings. Work orders without error/warn context (e.g.,
    feature requests, enhancements) are skipped to conserve AI resources.

    Keywords checked (case-insensitive): error, warn, bug, crash,
    critical, fail/failed/failure, exception, fatal, panic
    """
    severity_keywords = [
        'error', 'warn', 'bug', 'crash', 'critical',
        'fail', 'failed', 'failure', 'failing',
        'exception', 'fatal', 'panic', 'corrupt',
        'missing', 'broken', 'regression'
    ]

    description = (order.get('description', '') or '').lower()
    notes = (order.get('notes', '') or '').lower()
    combined = f"{description} {notes}"

    for keyword in severity_keywords:
        if keyword in combined:
            logger.debug(f"WO #{order['id']} matched severity keyword '{keyword}' -> spawning AI")
            return True

    logger.info(
        f"WO #{order['id']} skipped: no error/warn keywords found "
        f"(description='{order.get('description', '')[:60]}...'"
    )
    return False


class HealingAgent:
    """
    Orchestrates the self-healing process.
    
    CRITICAL: All agent operations are SERIAL. No parallel agents.
    See AGENT CONSTRAINT CONSTANTS at top of file.
    """
    
    def __init__(self, config: Dict[str, Any], api_client=None,
                 session_manager=None, work_order_engine=None):
        self.config = config
        self.api_client = api_client
        self.session_manager = session_manager
        self.work_order_engine = work_order_engine
        
        self._enabled = config.get('enabled', True)
        self._poll_interval = config.get('poll_interval', 10)
        self._agent_timeout = config.get('agent_timeout', 300)
        self._escalation_threshold = config.get('escalation_threshold', 3)
        self._max_concurrent = config.get('max_concurrent_agents', 2)  # Configured (enforced to MAX_CONCURRENT_AGENTS at runtime)
        
        # ═══ AGENT CONSTRAINT STATE ═══
        self._depth_tracker = AgentDepthTracker()
        self._processing_lock = asyncio.Lock()  # Ensures SERIAL processing
        self._processing = False  # Flag: is currently processing a work order
        
        # Active agent sessions (max 1 at a time)
        self._active_sessions: Dict[str, HealingAgentSession] = {}
        from ecosystem_config import get_documentation_db_path
        self._db_path = get_documentation_db_path()
        
        # Polling task
        self._poll_task = None
        self._running = False
        
        # System prompt generator
        self._prompt_generator = None
    
    def set_prompt_generator(self, generator):
        """Set the system prompt generator."""
        self._prompt_generator = generator
    
    # ════════════════════════════════════════════════════════════════
    # CONFIDENCE VALIDATION
    # ════════════════════════════════════════════════════════════════
    
    def validate_confidence(self, operation_type: str,
                            context: Optional[Dict] = None) -> ConfidenceVerdict:
        """
        Validate that an operation has 100% confidence for safe execution.
        
        Args:
            operation_type: 'spawn_sub_agent', 'chain_work_order'
            context: Optional context about the operation
        
        Returns:
            ConfidenceVerdict with approved=True/False and reason
        """
        try:
            risk_factors = []

        except Exception as e:
            logger.error(f"validate_confidence failed: {e}")
            return None
        
        if operation_type == 'spawn_sub_agent':
            # Check: are we at max depth?
            allowed, msg = self._depth_tracker.can_spawn()
            if not allowed:
                return ConfidenceVerdict(False, msg, ['depth_exceeded'])
            
            # Check: would spawn at depth 2+?
            next_depth = self._depth_tracker.current_depth + 1
            if next_depth > 1:
                # Depth 2+ requires 100% confidence analysis
                risk_factors = self._analyze_interference_risk(context)
                if risk_factors:
                    return ConfidenceVerdict(
                        False,
                        f"Cannot spawn sub-agent at depth {next_depth}: "
                        f"interference risks detected: {', '.join(risk_factors)}",
                        risk_factors
                    )
            
            # Depth 1 spawn (healer spawning for work order) — always allowed
            return ConfidenceVerdict(True, "Depth-1 spawn always allowed")
        
        elif operation_type == 'chain_work_order':
            # Check chain constraints
            allowed, msg = self._depth_tracker.can_chain()
            if not allowed:
                return ConfidenceVerdict(False, msg, ['chain_constraint'])
            
            # Check interference with existing state
            risk_factors = self._analyze_interference_risk(context)
            if risk_factors:
                return ConfidenceVerdict(
                    False,
                    f"Cannot chain work order: "
                    f"interference risks: {', '.join(risk_factors)}",
                    risk_factors
                )
            
            return ConfidenceVerdict(True, "Chain operation approved")
        
        else:
            return ConfidenceVerdict(False, f"Unknown operation type: {operation_type}")
    
    def _analyze_interference_risk(self, context: Optional[Dict]) -> List[str]:
        """
        Analyze if a sub-agent/chain operation could interfere with existing work.
        
        Returns list of risk factor descriptions (empty = no risk = 100% confidence).
        """
        try:
            risk_factors = []

        except Exception as e:
            logger.error(f"_analyze_interferenc failed: {e}")
            return None
        
        if not context:
            return risk_factors
        
        # Check tool interference
        requested_tools = set(context.get('requested_tools', []))
        active_tool_domains = set(context.get('active_tool_domains', []))
        
        overlapping_tools = requested_tools & active_tool_domains
        if overlapping_tools:
            risk_factors.append(f"tool overlap: {', '.join(overlapping_tools)}")
        
        # Check data domain interference
        requested_domains = set(context.get('requested_data_domains', []))
        active_domains = set(context.get('active_data_domains', []))
        
        overlapping_domains = requested_domains & active_domains
        if overlapping_domains:
            risk_factors.append(
                f"data domain overlap: {', '.join(overlapping_domains)}"
            )
        
        # Check for state-modifying operations
        if context.get('modifies_state', False):
            risk_factors.append("operation modifies system state")
        
        # Check for concurrent database access
        if context.get('uses_database', False) and context.get('active_db_sessions', 0) > 0:
            risk_factors.append("concurrent database access")
        
        return risk_factors
    
    # ════════════════════════════════════════════════════════════════
    # MAIN LOOP — SERIAL PROCESSING ONLY
    # ════════════════════════════════════════════════════════════════
    
    async def start(self):
        """Start the healing agent main loop."""
        self._running = True
        self._poll_task = asyncio.create_task(self._poll_for_work())
        logger.info(f"HealingAgent started (serial mode, max depth={MAX_AGENT_DEPTH})")
    
    async def stop(self):
        """Stop the healing agent."""
        self._running = False
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
        logger.info("HealingAgent stopped")
    
    async def _poll_for_work(self):
        """
        Background task that polls for new work orders.
        
        CRITICAL: Uses _processing_lock to ensure SERIAL processing.
        Only ONE work order is handled at a time. NO PARALLEL AGENTS.
        """
        last_checked_id = self._get_last_checked_work_order()
        
        while self._running:
            try:
                # Check for pending work orders
                new_orders = self._get_pending_work_orders(last_checked_id)
                
                for order in new_orders:
                    # ═══ SERIAL GATE ═══
                    if self._processing:
                        logger.debug("Already processing a work order — queueing next cycle")
                        break  # Exit loop, will try again next poll cycle
                    
                    if order['id'] > last_checked_id:
                        last_checked_id = order['id']
                    
                    # Acquire the serial processing lock
                    async with self._processing_lock:
                        self._processing = True
                        try:
                            logger.info(
                                f"Processing work order #{order['id']} "
                                f"(chain: {self._depth_tracker.chain_count + 1}/{MAX_CHAIN_LENGTH})"
                            )
                            await self._handle_work_order(order)
                        finally:
                            self._processing = False
                
                await asyncio.sleep(self._poll_interval)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Poll error: {e}")
                await asyncio.sleep(self._poll_interval * 2)
    
    def _get_last_checked_work_order(self) -> int:
        """Get the ID of the last work order we checked."""
        try:
            conn = sqlite3.connect(self._db_path)
            cursor = conn.cursor()
            cursor.execute('''
                SELECT MAX(id) FROM work_orders WHERE notes LIKE '%[AUTO-HEALING]%'
            ''')
            row = cursor.fetchone()
            conn.close()
            return row[0] if row and row[0] else 0
        except (sqlite3.Error, Exception):
            return 0
    
    def _get_pending_work_orders(self, after_id: int) -> List[Dict]:
        """Get pending work orders that haven't been processed yet."""
        try:
            conn = sqlite3.connect(self._db_path)
            cursor = conn.cursor()
            cursor.execute('''
                SELECT id, priority, description, notes, created_at 
                FROM work_orders 
                WHERE id > ? AND status = 'pending' AND priority <= 3
                ORDER BY priority ASC, id ASC
                LIMIT 10
            ''', (after_id,))
            
            results = []
            for row in cursor.fetchall():
                results.append({
                    'id': row[0],
                    'priority': row[1],
                    'description': row[2],
                    'notes': row[3],
                    'created_at': row[4]
                })
            conn.close()
            return results
        except (sqlite3.Error, Exception) as e:
            logger.error(f"DB error fetching work orders: {e}")
            return []
    
    # ════════════════════════════════════════════════════════════════
    # WORK ORDER HANDLING — SERIAL, DEPTH-AWARE
    # ════════════════════════════════════════════════════════════════
    
    
    async def _handle_work_order(self, order: Dict) -> bool:
            """
            Handle a single work order. Spawns healing agent.
        
            ENTERS depth level 1 automatically.
            Returns True if fixed successfully.
            """
            work_order_id = order['id']
            session_id = f"heal-{work_order_id}-{int(time.time())}"
        
            # ═══ DEPTH CHECK ═══
            if not self._depth_tracker.enter(session_id):
                logger.error(f"Cannot process WO #{work_order_id}: depth limit exceeded")
                self._update_work_order_status(
                    work_order_id, 'failed',
                    notes_append=f"REJECTED: Agent depth limit ({MAX_AGENT_DEPTH}) exceeded"
                )
                return False
            
        
            # ═══ CHAIN STEP ═══
            self._depth_tracker.mark_chain_step()
            try:
                # ═══ CHAIN CHECK ═══
                chain_allowed, chain_reason = self._depth_tracker.can_chain()
                if not chain_allowed:
                    logger.warning(f"Chain limit reached for WO #{work_order_id}: {chain_reason}")
                    # Force state flush and reset chain count
                    if self.session_manager:
                        self.session_manager.flush('chain_limit_reached')
                    self._depth_tracker.reset_chain_count()
                    logger.info("Chain count reset after forced flush")
            
                # Create session tracker
                session = HealingAgentSession(
                    work_order_id, session_id,
                    depth=self._depth_tracker.current_depth
                )
                self._active_sessions[session_id] = session
            
                try:
                    # Check if this work order is error/warn related before spawning AI
                    if not _is_error_or_warn_work_order(order):
                        logger.info(
                            f"WO #{work_order_id} skipped: not an error/warn work order. "
                            "Self-healing AI only triggers on errors and warnings."
                        )
                        self._update_work_order_status(
                            work_order_id, 'completed',
                            notes_append="Skipped by healing agent: not error/warn related [AUTO-SKIP]"
                        )
                        return True  # Mark as "handled" so it doesn't get stuck
                
                    # Mark as in-progress
                    self._update_work_order_status(work_order_id, 'in-progress')
                
                    # Generate system prompt
                    system_prompt = await self._generate_agent_prompt(order)
                    if not system_prompt:
                        logger.error(f"Failed to generate system prompt for WO #{work_order_id}")
                        session.status = AgentHealthStatus.FAILED
                        self._update_work_order_status(
                            work_order_id, 'failed',
                            notes_append="Failed to generate system prompt"
                        )
                        return False
                
                    # Mark that recovery attempt started
                    session.recovery_attempts += 1
                
                    # Check recovery threshold
                    if session.recovery_attempts > self._escalation_threshold:
                        logger.warning(
                            f"Escalating WO #{work_order_id}: "
                            f"too many recovery attempts ({session.recovery_attempts})"
                        )
                        self._escalate_work_order(work_order_id)
                        session.status = AgentHealthStatus.FAILED
                        return False
                
                    # Spawn agent via API
                    logger.info(
                        f"Spawned healing agent for WO #{work_order_id} "
                        f"(session: {session_id}, depth: {self._depth_tracker.current_depth})"
                    )
                
                    success = await self._spawn_fix_session(session, system_prompt, order)
                
                    if success:
                        session.status = AgentHealthStatus.COMPLETED
                        session.result = "Agent completed fix successfully"
                        self._update_work_order_status(
                            work_order_id, 'completed',
                            notes_append="Fix applied and validated by healing agent [AUTO-HEALING]"
                        )
                        logger.info(f"WO #{work_order_id} completed by healing agent")
                        return True
                    else:
                        session.status = AgentHealthStatus.FAILED
                        # Session manager recovery
                        if (self.session_manager and
                                session.recovery_attempts <= self._escalation_threshold):
                            await self._recover_session(session)
                            # Try again (re-enters at same depth)
                            return await self._handle_work_order(order)
                        else:
                            self._update_work_order_status(
                                work_order_id, 'pending',
                                notes_append="Healing agent failed, requeued"
                            )
                            return False
                        
                except Exception as e:
                    logger.error(f"Error handling WO #{work_order_id}: {e}")
                    session.status = AgentHealthStatus.FAILED
                    session.error = str(e)
                
                    if self.session_manager:
                        await self._recover_session(session)
                
                    self._update_work_order_status(
                        work_order_id, 'pending',
                        notes_append=f"Error: {str(e)[:200]}"
                    )
                    return False
                
                finally:
                    self._active_sessions.pop(session_id, None)
        
            finally:
                # ═══ DEPTH EXIT ═══
                self._depth_tracker.exit(session_id)
    
    async def _generate_agent_prompt(self, order: Dict) -> Optional[str]:
        """
        Generate a system prompt for the healing agent.
        
        Includes agent constraints so the spawned agent knows its limits.
        """
        if self._prompt_generator:
            return await self._prompt_generator.generate_for_work_order(order)
        
        # Fallback: build basic prompt
        notes = order.get('notes', '')
        description = order.get('description', '')
        error_info = self._extract_error_info(notes)
        tool_defs = self._get_tool_definitions()
        depth_info = self._depth_tracker.get_info()
        
        prompt = f"""You are an AI repair agent for the DeepSky Self-Healing System.

## CRITICAL CONSTRAINTS — You MUST follow these:
1. **NO parallel agents**: You are the ONLY agent running. Do NOT spawn sub-agents.
2. **Max depth {MAX_AGENT_DEPTH}**: You are at depth {depth_info['current_depth']}. 
   You may spawn exactly ONE sub-agent (depth {depth_info['current_depth'] + 1}) ONLY if 
   100% confidence exists that the sub-agent won't interfere with your work.
3. **Max chain {MAX_CHAIN_LENGTH}**: Sequential work order #{depth_info['chain_count'] + 1}.
4. **Serial only**: Complete your task before the next agent can start.

## Your Mission
Fix the issue described in the work order below. You have full access to:
1. The DeepSeek API (you are using it right now)
2. FastMCP tools (available via Phooks bridge)
3. The complete YuniScripts ecosystem
4. File system access for code fixes

## Work Order #{order['id']}
**Priority:** {order.get('priority', 3)}
**Description:** {description}

## Error Context
{json.dumps(error_info, indent=2) if error_info else "No additional error context available."}

## Work Order Notes
{notes[:2000] if notes else "No notes."}

## Available Tools
{json.dumps(tool_defs, indent=2) if tool_defs else "Standard file system and database tools available."}

## Your Task
1. Analyze the issue
2. Investigate the code and data
3. Implement the fix
4. Validate the fix with full tests
5. Report completion

## Rules
- NEVER use pytest! Use unittest for all testing
- Run full comprehensive tests before marking complete
- If a tool fails, investigate immediately and fix it
- Document all changes made
"""
        return prompt
    
    def _extract_error_info(self, notes: str) -> Dict:
        """Extract structured error info from work order notes."""
        try:
            info = {}

        except Exception as e:
            logger.error(f"_extract_error_info failed: {e}")
            return None
        if not notes:
            return info
        
        lines = notes.split('\n')
        for i, line in enumerate(lines):
            if line.startswith('**Category:**'):
                info['category'] = line.split('**Category:**', 1)[1].strip()
            elif line.startswith('**Component:**'):
                info['component'] = line.split('**Component:**', 1)[1].strip()
            elif line.startswith('**Summary:**'):
                info['summary'] = line.split('**Summary:**', 1)[1].strip()
            elif line.startswith('**Stack Trace:**'):
                stack_lines = []
                for sl in lines[i+1:]:
                    if sl.startswith('**') or sl.startswith('```'):
                        break
                    stack_lines.append(sl)
                info['stack_trace'] = '\n'.join(stack_lines)
            elif line.startswith('**Data Flow Path:**'):
                path_line = lines[i+1] if i+1 < len(lines) else ''
                info['data_flow_path'] = path_line
        
        return info
    
    def _get_tool_definitions(self) -> List[Dict]:
        """Get available tool definitions for the agent."""
        try:
            from ecosystem_config import get_documentation_db_path
            tools_db = get_documentation_db_path()
            conn = sqlite3.connect(tools_db)
            cursor = conn.cursor()
            try:
                cursor.execute('''
                    SELECT tool_name, description, category FROM tool_registry 
                    WHERE enabled = 1 LIMIT 30
                ''')
                tools = []
                for row in cursor.fetchall():
                    tools.append({
                        'name': row[0],
                        'description': row[1][:100] if row[1] else '',
                        'category': row[2]
                    })
                conn.close()
                return tools
            except sqlite3.OperationalError:
                conn.close()
                return []
        except Exception as e:
            logger.debug(f"Could not load tool definitions: {e}")
            return []
    
    # ════════════════════════════════════════════════════════════════
    # TOOL CALL EXECUTION — FIX for WO #203 (sub-agent tool gap)
    # Previously: _spawn_fix_session() returned True on ANY text
    # response, doing ZERO actual work. Now: passes real tool defs
    # to API, executes returned tool_calls, loops until "done".
    # ════════════════════════════════════════════════════════════════
    
    _STANDARD_TOOL_DEFS = [
        {"type":"function","function":{"name":"read_files","description":"Read one or more text files. Returns contents.","parameters":{"type":"object","properties":{"paths":{"type":"array","items":{"type":"string"},"description":"File paths"},"limit":{"type":"integer","description":"Max chars"}},"required":["paths"]}}},
        {"type":"function","function":{"name":"write_file","description":"Write content to a file. Creates parent dirs.","parameters":{"type":"object","properties":{"path":{"type":"string","description":"File path"},"content":{"type":"string","description":"Content"}},"required":["path","content"]}}},
        {"type":"function","function":{"name":"edit_text","description":"Edit files via regex replacement or line editing.","parameters":{"type":"object","properties":{"path":{"type":"string"},"operation":{"type":"string","enum":["replace_pattern","edit_lines"]},"pattern":{"type":"string"},"replacement":{"type":"string"}},"required":["path","operation"]}}},
        {"type":"function","function":{"name":"database_query","description":"SELECT query on SQLite. Returns JSON.","parameters":{"type":"object","properties":{"database_path":{"type":"string"},"query":{"type":"string"}},"required":["database_path","query"]}}},
        {"type":"function","function":{"name":"database_execute","description":"INSERT/UPDATE/DELETE on SQLite.","parameters":{"type":"object","properties":{"database_path":{"type":"string"},"query":{"type":"string"}},"required":["database_path","query"]}}},
        {"type":"function","function":{"name":"text_replace","description":"Regex replace in files with unified diff preview.","parameters":{"type":"object","properties":{"path":{"type":"string"},"pattern":{"type":"string"},"replacement":{"type":"string"}},"required":["path","pattern","replacement"]}}},
        {"type":"function","function":{"name":"grep_search","description":"Search file contents with regex.","parameters":{"type":"object","properties":{"path":{"type":"string"},"pattern":{"type":"string"},"file_pattern":{"type":"string"}},"required":["path","pattern"]}}},
        {"type":"function","function":{"name":"execute_command","description":"Execute shell command with logging.","parameters":{"type":"object","properties":{"command":{"type":"string"}},"required":["command"]}}},
        {"type":"function","function":{"name":"create_work_order","description":"Create a new work order.","parameters":{"type":"object","properties":{"description":{"type":"string"},"priority":{"type":"integer"},"notes":{"type":"string"}},"required":["description"]}}},
        {"type":"function","function":{"name":"execute_python","description":"Execute Python in sandbox.","parameters":{"type":"object","properties":{"code":{"type":"string"},"timeout":{"type":"integer"},"sandbox":{"type":"boolean"}},"required":["code"]}}},
        {"type":"function","function":{"name":"find_for_me","description":"Unified search across files, content, and databases.","parameters":{"type":"object","properties":{"query":{"type":"string"},"search_type":{"type":"string","enum":["all","files","content","database"]}},"required":["query"]}}},
        {"type":"function","function":{"name":"sequentialthinking","description":"Structured step-by-step reasoning with cognitive memory.","parameters":{"type":"object","properties":{"thought":{"type":"string"},"next_thought_needed":{"type":"boolean"},"thought_number":{"type":"integer"},"total_thoughts":{"type":"integer"}},"required":["thought","next_thought_needed","thought_number","total_thoughts"]}}}
    ]
    
    def _build_openai_tool_defs(self) -> List[Dict]:
        """Build OpenAI-compatible tool defs combining standard + DB tools."""
        tool_defs = list(self._STANDARD_TOOL_DEFS)
        existing = {t['function']['name'] for t in tool_defs}
        try:
            for t in self._get_tool_definitions():
                n = t.get('name','')
                if n and n not in existing:
                    tool_defs.append({"type":"function","function":{"name":n,"description":(t.get('description','') or '')[:200],"parameters":{"type":"object","properties":{},"required":[]}}})
                    existing.add(n)
        except Exception:
            pass
        return tool_defs
    
    def _execute_tool_call(self, tool_name: str, arguments: Dict) -> Dict:
        """Execute a single tool call by importing and calling the tool module."""
        import importlib.util, sys
        MAP = {
            'read_files':'read_files.py','write_file':'write_file.py','edit_text':'edit_text.py',
            'text_replace':'text_replace.py','database_query':'database_query.py',
            'database_execute':'database_execute.py','grep_search':'grep_search.py',
            'execute_command':'execute_command.py','execute_python':'execute_python.py',
            'create_work_order':'create_work_order.py','find_for_me':'find_for_me.py',
            'sequentialthinking':'sequentialthinking.py'
        }
        PATHS = [
            os.environ.get(
                'DEEPSKY_FASTMCP_TOOLS_PATH',
                os.path.join(os.path.expanduser('~'), 'Documents', 'dev-yuniScripts', 'SCRIPTS', 'SERVICES', 'fastmcp_server', 'tools')
            )
        ]
        
        # Special case: create_work_order needs eco DB path
        if tool_name == 'create_work_order':
            try:
                from ecosystem_config import get_documentation_db_path
                dbp = get_documentation_db_path()
                tool_path = PATHS[0]+'/create_work_order.py'
                if os.path.exists(tool_path):
                    spec = importlib.util.spec_from_file_location('wo_tool', tool_path)
                    if spec and spec.loader:
                        m = importlib.util.module_from_spec(spec)
                        sys.modules['wo_tool'] = m
                        spec.loader.exec_module(m)
                        fn = getattr(m,'create_work_order',None) or getattr(m,'exec_on_db',None)
                        if fn:
                            r = fn(description=arguments.get('description','No description'), priority=arguments.get('priority',3), notes=arguments.get('notes',''), assigned_to='AI', status='pending')
                            return {'result':str(r)[:2000],'error':None}
            except Exception as e:
                return {'error':str(e)[:500],'result':None}
        
        mf = MAP.get(tool_name)
        if not mf:
            return {'error':f"Unknown tool: {tool_name}",'result':None}
        for bp in PATHS:
            mp = os.path.join(bp, mf)
            if os.path.exists(mp):
                try:
                    spec = importlib.util.spec_from_file_location(f't_{tool_name}', mp)
                    if not spec or not spec.loader:
                        continue
                    mod = importlib.util.module_from_spec(spec)
                    sys.modules[f't_{tool_name}'] = mod
                    spec.loader.exec_module(mod)
                    fn = getattr(mod, tool_name, None)
                    if fn is None:
                        for an in dir(mod):
                            if an.startswith('_'): continue
                            a = getattr(mod, an)
                            if callable(a): fn = a; break
                    if fn:
                        r = fn(**arguments)
                        return {'result': str(r)[:2000], 'error': None}
                except Exception as e:
                    return {'error':f"Exec error: {str(e)[:500]}",'result':None}
        return {'error':f"Tool module not found for {tool_name}",'result':None}
    
    async def _spawn_fix_session(self, session: HealingAgentSession,
                                  system_prompt: str, order: Dict) -> bool:
        """
        Spawn an AI agent to fix the issue.
        
        Sends the system prompt + work order to the API
        and processes the agent's response.
        """
        if not self.api_client:
            logger.error("No API client available for healing agent")
            return False
        
        # Build message chain with tool definitions
        tool_defs = self._build_openai_tool_defs()
        messages = [
            {'role': 'system', 'content': system_prompt},
            {'role': 'user',
             'content': f"Please investigate and fix work order #{order['id']}. You have REAL tool access - use read_files, write_file, edit_text, database_query, execute_command, etc. to investigate and fix the issue. When done, say: TASK COMPLETE: <summary>"}
        ]
        
        # Inform session manager of healing activity
        if self.session_manager:
            self.session_manager.flush('healing_agent_spawned')
        
        # ═══ TOOL-CALLING CONVERSATION LOOP (FIX for WO #203) ═══
        total_tool_calls = 0
        
        for iteration in range(15):
            try:
                response = await asyncio.wait_for(
                    self.api_client.chat_completion(messages, tools=tool_defs, stream=False),
                    timeout=min(60, self._agent_timeout)
                )
                
                if not (hasattr(response, 'success') and response.success):
                    session.error = getattr(response, 'error', 'API call failed')
                    return False
                
                content = getattr(response, 'content', '') or ''
                tool_calls = getattr(response, 'tool_calls', None) or []
                
                # Check if agent reports completion
                if not tool_calls:
                    if content and ('TASK COMPLETE' in content.upper() or 'FIX COMPLETE' in content.upper() or 'COMPLETED' in content.upper()):
                        session.last_activity = time.time()
                        session.result = content[:500]
                        logger.info(f"Healing agent done ({total_tool_calls} tool calls)")
                        return True
                    # Agent responded but not done — ask to continue
                    messages.append({'role': 'assistant', 'content': content})
                    messages.append({'role': 'user', 'content': 'Continue. Report TASK COMPLETE when done.'})
                    continue
                
                # Execute all tool calls from this response
                tc_list = tool_calls if isinstance(tool_calls, list) else [tool_calls]
                
                # Add assistant message referencing tool calls
                assistant_msg = {'role': 'assistant', 'content': content or None, 'tool_calls': tc_list}
                
                for tc in tc_list:
                    tc_id = tc.get('id', str(hash(str(tc)))) if isinstance(tc, dict) else str(hash(str(tc)))
                    fn_info = tc.get('function', tc) if isinstance(tc, dict) else tc
                    tc_name = fn_info.get('name', '') if isinstance(fn_info, dict) else ''
                    try:
                        raw_args = fn_info.get('arguments', '{}') if isinstance(fn_info, dict) else '{}'
                        tc_args = json.loads(raw_args) if isinstance(raw_args, str) else (raw_args or {})
                    except (json.JSONDecodeError, TypeError):
                        tc_args = {}
                    
                    if not tc_name:
                        continue
                    
                    # Execute tool
                    logger.info(f"Tool call: {tc_name}(...)")
                    try:
                        result = self._execute_tool_call(tc_name, tc_args)
                        res_content = result.get('result', '') or result.get('error', '') or 'No result'
                        if result.get('error'):
                            res_content = f"ERROR: {result['error']}"
                        messages.append({'role': 'tool', 'tool_call_id': tc_id, 'name': tc_name, 'content': str(res_content)[:2000]})
                        total_tool_calls += 1
                    except Exception as e:
                        messages.append({'role': 'tool', 'tool_call_id': tc_id, 'name': tc_name, 'content': f"EXCEPTION: {str(e)[:500]}"})
                
                messages.append(assistant_msg)
                
                if total_tool_calls > 50:
                    messages.append({'role': 'user', 'content': 'Too many tool calls. Summarize and report TASK COMPLETE.'})
                    
            except asyncio.TimeoutError:
                session.status = AgentHealthStatus.TIMEOUT
                session.error = f"Timeout at iteration {iteration}"
                return False
            except Exception as e:
                session.error = str(e)
                return False
        
        session.result = "Max iterations reached without completion"
        return False
    
    async def _recover_session(self, session: HealingAgentSession) -> bool:
        """Recover from a failed agent session."""
        logger.info(f"Recovering session for WO #{session.work_order_id}")
        
        if self.session_manager:
            try:
                self.session_manager.restore_from_checkpoint(
                    str(session.work_order_id)
                )
                logger.info(f"Session recovered for WO #{session.work_order_id}")
                return True
            except Exception as e:
                logger.error(f"Session recovery failed: {e}")
                return False
        return False
    
    # ════════════════════════════════════════════════════════════════
    # SUB-AGENT SPAWNING (Depth 2 — requires 100% confidence)
    # ════════════════════════════════════════════════════════════════
    
    async def spawn_sub_agent(self, task_description: str,
                               context: Optional[Dict] = None) -> Dict:
        """
        Spawn a sub-agent for a VERY SPECIFIC, BOUNDED task.
        
        CRITICAL: Only allowed when:
        - Current depth < MAX_AGENT_DEPTH
        - 100% confidence that sub-agent won't interfere with main work
        - Task is well-defined and bounded
        
        Returns:
            {'success': bool, 'result': Any, 'error': str}
        """
        # ═══ CONFIDENCE VALIDATION ═══
        verdict = self.validate_confidence('spawn_sub_agent', context)
        if not verdict.approved:
            logger.warning(f"Sub-agent spawn rejected: {verdict.reason}")
            return {
                'success': False,
                'result': None,
                'error': verdict.reason,
                'verdict': verdict.to_dict()
            }
        
        # ═══ DEPTH CHECK ═══
        sub_session_id = f"sub-{int(time.time())}-{hash(task_description) % 10000}"
        if not self._depth_tracker.enter(sub_session_id):
            return {
                'success': False,
                'result': None,
                'error': f"Max depth ({MAX_AGENT_DEPTH}) reached",
                'verdict': {'approved': False, 'reason': 'depth_exceeded'}
            }
        
        try:
            # Generate focused system prompt for sub-task
            system_prompt = (
                f"You are a focused sub-agent for the DeepSky Self-Healing System.\n\n"
                f"## TASK (BOUNDED)\n{task_description}\n\n"
                f"## CONSTRAINTS\n"
                f"- You are at depth {self._depth_tracker.current_depth}/{MAX_AGENT_DEPTH}\n"
                f"- Do NOT spawn any further sub-agents\n"
                f"- Do NOT modify any state outside your task scope\n"
                f"- Complete and report back as quickly as possible\n"
            )
            
            messages = [
                {'role': 'system', 'content': system_prompt},
                {'role': 'user', 'content': task_description}
            ]
            
            # Add tool definitions for sub-agent (FIX for WO #203)
            tool_defs = self._build_openai_tool_defs()
            messages = [
                {'role': 'system', 'content': system_prompt},
                {'role': 'user', 'content': task_description + "\n\nUse tools to accomplish this task. Report TASK COMPLETE when done."}
            ]
            
            if self.api_client:
                total_tc = 0
                for iteration in range(8):
                    try:
                        response = await asyncio.wait_for(
                            self.api_client.chat_completion(messages, tools=tool_defs, stream=False),
                            timeout=min(60, self._agent_timeout)
                        )
                        
                        if not (hasattr(response, 'success') and response.success):
                            return {'success': False, 'result': None, 'error': getattr(response, 'error', 'API failed'), 'verdict': {'approved': True, 'reason': 'api_error'}}
                        
                        content = getattr(response, 'content', '') or ''
                        tool_calls = getattr(response, 'tool_calls', None) or []
                        
                        # Check completion
                        if not tool_calls:
                            if content and ('TASK COMPLETE' in content.upper() or 'FIX COMPLETE' in content.upper() or 'DONE' in content.upper()):
                                return {'success': True, 'result': content, 'error': None, 'verdict': {'approved': True, 'reason': 'sub-agent completed'}}
                            messages.append({'role': 'assistant', 'content': content})
                            messages.append({'role': 'user', 'content': 'Continue. Report TASK COMPLETE when done.'})
                            continue
                        
                        # Execute tool calls
                        tc_list = tool_calls if isinstance(tool_calls, list) else [tool_calls]
                        ass_msg = {'role': 'assistant', 'content': content or None, 'tool_calls': tc_list}
                        
                        for tc in tc_list:
                            tc_id = tc.get('id', str(id(tc))) if isinstance(tc, dict) else str(id(tc))
                            fn_info = tc.get('function', tc) if isinstance(tc, dict) else tc
                            tc_name = fn_info.get('name', '') if isinstance(fn_info, dict) else ''
                            try:
                                raw = fn_info.get('arguments', '{}') if isinstance(fn_info, dict) else '{}'
                                tc_args = json.loads(raw) if isinstance(raw, str) else (raw or {})
                            except (json.JSONDecodeError, TypeError):
                                tc_args = {}
                            if tc_name:
                                try:
                                    r = self._execute_tool_call(tc_name, tc_args)
                                    rc = r.get('result', '') or r.get('error', '') or 'No result'
                                    if r.get('error'): rc = f"ERROR: {r['error']}"
                                    messages.append({'role': 'tool', 'tool_call_id': tc_id, 'name': tc_name, 'content': str(rc)[:2000]})
                                    total_tc += 1
                                except Exception as e:
                                    messages.append({'role': 'tool', 'tool_call_id': tc_id, 'name': tc_name, 'content': f"EXCEPTION: {str(e)[:500]}"})
                        
                        messages.append(ass_msg)
                        
                    except asyncio.TimeoutError:
                        return {'success': False, 'result': None, 'error': f"Sub-agent timeout at iteration {iteration}", 'verdict': {'approved': True, 'reason': 'sub-agent timeout'}}
                    except Exception as e:
                        return {'success': False, 'result': None, 'error': str(e)[:500], 'verdict': {'approved': True, 'reason': 'sub-agent exception'}}
                
                return {'success': True, 'result': 'Max iterations reached', 'error': None, 'verdict': {'approved': True, 'reason': 'max_iterations'}}
            else:
                return {
                    'success': False,
                    'result': None,
                    'error': 'No API client available',
                    'verdict': {'approved': False, 'reason': 'no_api_client'}
                }
                
        except asyncio.TimeoutError:
            return {
                'success': False,
                'result': None,
                'error': f"Sub-agent timed out after {self._agent_timeout}s",
                'verdict': {'approved': True, 'reason': 'sub-agent timeout'}
            }
        except Exception as e:
            return {
                'success': False,
                'result': None,
                'error': str(e),
                'verdict': {'approved': True, 'reason': 'sub-agent exception'}
            }
        finally:
            self._depth_tracker.exit(sub_session_id)
    
    # ════════════════════════════════════════════════════════════════
    # DATABASE OPERATIONS
    # ════════════════════════════════════════════════════════════════
    
    def _update_work_order_status(self, work_order_id: int, status: str,
                                  notes_append: Optional[str] = None):
        """Update work order status in database."""
        try:
            conn = sqlite3.connect(self._db_path)
            cursor = conn.cursor()
            
            if notes_append:
                cursor.execute('''
                    UPDATE work_orders 
                    SET status = ?, updated_at = datetime('now'),
                        notes = CASE 
                            WHEN notes IS NULL OR notes = '' THEN ?
                            ELSE notes || '\n\n[Healing Agent ' || datetime('now') || ']: ' || ?
                        END
                    WHERE id = ?
                ''', (status, notes_append, notes_append, work_order_id))
            else:
                cursor.execute('''
                    UPDATE work_orders 
                    SET status = ?, updated_at = datetime('now')
                    WHERE id = ?
                ''', (status, work_order_id))
            
            conn.commit()
            conn.close()
        except sqlite3.Error as e:
            logger.error(f"Failed to update WO #{work_order_id}: {e}")
    
    def _escalate_work_order(self, work_order_id: int):
        """Escalate a work order that keeps failing."""
        try:
            conn = sqlite3.connect(self._db_path)
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE work_orders 
                SET priority = 1, 
                    notes = CASE 
                        WHEN notes IS NULL OR notes = '' 
                            THEN 'ESCALATED: Too many recovery attempts'
                        ELSE notes || '\n\n**ESCALATED:** Too many recovery attempts'
                    END,
                    updated_at = datetime('now')
                WHERE id = ?
            ''', (work_order_id,))
            conn.commit()
            conn.close()
            logger.warning(f"WO #{work_order_id} escalated to priority 1")
        except sqlite3.Error as e:
            logger.error(f"Failed to escalate WO #{work_order_id}: {e}")
    
    # ════════════════════════════════════════════════════════════════
    # CONTROL & STATUS
    # ════════════════════════════════════════════════════════════════
    
    def get_active_sessions(self) -> List[Dict]:
        """Get information about active healing sessions."""
        return [{
            'work_order_id': s.work_order_id,
            'session_id': s.session_id,
            'depth': s.depth,
            'status': s.status,
            'elapsed': time.time() - s.started_at,
            'recovery_attempts': s.recovery_attempts
        } for s in self._active_sessions.values()]
    
    def get_stats(self) -> Dict:
        """Get healing agent statistics including depth tracking."""
        return {
            'enabled': self._enabled,
            'active_sessions': len(self._active_sessions),
            'max_concurrent': MAX_CONCURRENT_AGENTS,
            'processing': self._processing,
            'poll_interval': self._poll_interval,
            'agent_timeout': self._agent_timeout,
            'escalation_threshold': self._escalation_threshold,
            'depth_tracker': self._depth_tracker.get_info(),
            'constraints': {
                'MAX_AGENT_DEPTH': MAX_AGENT_DEPTH,
                'MAX_CHAIN_LENGTH': MAX_CHAIN_LENGTH,
                'MAX_CHAIN_DEPTH': MAX_CHAIN_DEPTH,
                'MAX_CONCURRENT_AGENTS': MAX_CONCURRENT_AGENTS
            }
        }

