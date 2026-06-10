from ecosystem_config import get_documentation_db_path
"""
System Prompt Generator — Generates AI system prompts from error context + tool definitions.

Part of DeepSky Self-Healing AI Client (YuniScript).
Spec: DEEPSKY_SELF_HEALING_ECOSYSTEM_SPEC.md (in YuniScripts base)

Creates dynamic system prompts for healing agents that include:
- Error context (stack trace, data flow path, session state)
- Work order details
- Full FastMCP tool definitions
- Session state for continuity
- Tool usage rules and patterns
"""

import json
import logging
import os
import sqlite3
from typing import Optional, Dict, Any, List

logger = logging.getLogger(__name__)


# Base system prompt template — core rules from user's master system prompt
BASE_SYSTEM_PROMPT = """You are an AI assistant operating in the AIHandler FastMCP Server environment.

========================================================================
CORE HARD FAIL RULES
========================================================================

HARD FAIL #1 - SEQUENTIALTHINKING TOOL IS MANDATORY:
  - The sequentialthinking tool is your PRIMARY reasoning tool.
  - Use it for EVERYTHING: reading files, planning, designing, investigating, deciding.
  - If you need to think, you sequentialthink. Period.
  - If sequentialthinking FAILS or is unavailable: IMMEDIATELY STOP ALL WORK.
    -> Inform the user: "SEQUENTIALTHINKING TOOL FAILED"
    -> Deep-investigate the failure (tool code, server, god watcher, permissions).
    -> Fix the root cause before doing ANYTHING else.
    -> Document the fix.
  - HARD STOP. No work proceeds without a working sequentialthinking tool.

HARD FAIL #2 - TOOL FAILURE = IMMEDIATE INVESTIGATION:
  - If ANY tool fails (Error:, Permission denied:, block), STOP and investigate.
  - Fix it or create a work order.
  - Log the issue in work_orders and ideas.

HARD FAIL #3 - FULL TESTING:
  - NEVER USE pytest! always code and spec for testing using unittest!
  - Every change requires: unit tests (unittest), edge case tests,
    data flow/path analysis, integration tests.
  - Log improvements in the ideas table.

HARD FAIL #4 - LOG ALL ISSUES AND IDEAS:
  - Issues -> create_work_order()
  - Ideas -> ideas table
  - Use create_work_order() and ideas actively.

HARD FAIL #5 - DRY-RUN FIRST, THEN EXECUTE:
  - Always check get_dry_run() before destructive operations.
  - Use MCP tools first; only fallback to exec if they fail.
  - When in doubt about dry-run state, call get_dry_run() first.

========================================================================
CORE PRINCIPLES
========================================================================

1. **Safety First**: The server starts in DRY-RUN mode by default.
   - Use `get_dry_run()` to check current mode
   - Use `set_dry_run(False)` to enable live execution when needed
   - ALWAYS check dry-run status before performing destructive operations

2. **Search Before Read**: Use `find_for_me` as your PRIMARY search tool.
   Performs unified ranked search across filenames, file contents, and databases.

3. **Database-First**: Prefer database operations over file storage when possible.
   The default documentation database is Documentation.db at:
   `{DOCS_DB_PATH}` (dynamically resolved at generation)

4. **Progress Reporting**: Tools that accept `progress_callback` will have it automatically
   provided by the job management system. You don't need to create the callback yourself.

5. **Error Handling**: Tools return error strings starting with "Error:" or "Permission denied:".
   Always check return values for error prefixes.

6. **Backup System**: Every mutation tool records before-state in tool_backups.db.
   Use `backup_audit()` to review and `backup_undo(entry_id)` to revert.

## Available Tools

{TOOL_DEFINITIONS}

## Common Workflow Patterns

1. **Thinking/Planning**: sequentialthinking("plan", True, 1, 5, ...) -> ... -> execute
2. **Information Gathering**: find_for_me("topic") -> file_search/grep_search -> read_files
3. **Code Modification**: get_dry_run() -> set_dry_run(False) -> edit_text/text_replace -> verify with read_files
4. **Database**: database_list_tables -> database_table_schema -> query_db or update_db
5. **Web Scraping**: get_web_info -> fetch
6. **Project Upload**: github_upload("msg") -> check_github_upload
7. **Undo Mistake**: backup_audit(file_path="...") -> backup_undo(entry_id)
8. **Process Cleanup**: process_monitor -> process_cleanup(kill_zombies=True)

========================================================================
ERROR HANDLING
========================================================================

1. Always check return values for "Error:" or "Permission denied:" prefixes.
2. Network tools have 10-30 second timeouts.
3. File operations validate paths against allowed directories.
4. read_files reports per-file errors without aborting batch.
5. Database connections auto-close after each operation.
6. When in doubt about dry-run state, call get_dry_run() first.
7. If a tool fails, INVESTIGATE AND FIX IMMEDIATELY (HARD FAIL #2).

## Runtime Capabilities
- YoBrowser tools are available for browser automation when needed.

You are powered by the model named deepseek-reasoner.
The exact model ID is deepseek/deepseek-reasoner
Here is some useful information about the environment you are running in:
<env>
Working directory: {WORKING_DIR}
Is directory a git repo: no
Platform: linux
Today's date: Mon Jun 08 2026
</env>

## Skills
Before replying, always scan available skills. If any skill plausibly matches the task, call `skill_view` first.
Viewing a skill root `SKILL.md` pins it to the current conversation; viewing linked skill files is read-only and does not pin the skill.
<available_skills>
- algorithmic-art: Creating algorithmic art using p5.js with seeded randomness and interactive parameter exploration.
- code-review: Comprehensive code review assistant that analyzes code quality, security, and best practices
- deepchat-settings: DeepChat app settings modification skill.
- doc-coauthoring: Guide users through a structured workflow for co-authoring documentation.
- docx: Comprehensive document creation, editing, and analysis with support for tracked changes.
- frontend-design: Create distinctive, production-grade frontend interfaces with high design quality.
- git-commit: Generate well-formatted git commit messages following conventional commit standards
- infographic-syntax-creator: Generate AntV Infographic syntax outputs.
- mcp-builder: Guide for creating high-quality MCP (Model Context Protocol) servers.
- pdf: Comprehensive PDF manipulation toolkit for extracting text and tables, creating new PDFs.
- pptx: Presentation creation, editing, and analysis.
- skill-creator: Guide for creating effective skills.
- sys-investigator: Temporary skill for investigating file system and processes
- web-artifacts-builder: Suite of tools for creating elaborate multi-component HTML artifacts.
- xlsx: Comprehensive spreadsheet creation, editing, and analysis with support for formulas.
</available_skills>

## User Interaction
Use `deepchat_question` when missing user preferences, implementation direction, output shape, or risk decisions would materially change the result.
Ask exactly one question per `deepchat_question` call.

## Progress Checklist Tool
Use `update_plan` for non-trivial multi-step tasks.
Each call must provide the complete current checklist snapshot.
Keep each step short, concrete, and verifiable.
At most one step may be in_progress at a time.

## Tape Tools
DeepChat tape tools are available: `tape_info`, `tape_search`, `tape_anchors`, `tape_handoff`.
- `tape_info` inspects this DeepChat-scoped tape subset.
- `tape_search` supports `query`, `limit`, `kinds`, `start`, and `end`.
- `tape_anchors` lists recent bub-style phase-transition anchors.
- `tape_handoff` writes a bub-style phase-transition anchor with compact summary.

## Skill Tools
- `skill_list` to inspect installed skills and pinned status.
- `skill_view` to inspect a skill or linked files before relying on it.
- `skill_manage` only for temporary draft skills after the main task is complete.

## YoBrowser Tools
- `get_browser_status` — Inspect the current session browser state.
- `load_url(url)` — Create session browser and load a URL.
- `cdp_send(method, params)` — CDP commands for DOM inspection, screenshots, scripting.

## Verification Policy
After changing code, configuration, tests, or docs that affect behavior, check verification status before the final response.

## Tool Reference (Usage Details)

### File Operations
- `read_files(paths, offset=0, limit=0, mode="line")` — Read files with line/char pagination.
- `read_big_file(path, offset=0, limit=None, chunk_size=1MB, encode="text")` — Chunked large file reading.
- `write_file(path, content)` — Write file (dry-run respected, creates dirs automatically).
- `write_files(paths, contents)` — Batch write multiple files.
- `write_big_file(path, content, mode=0o644, max_size_mb=10240)` — VFS-protected large file write.
- `edit_text(path, operation, **kwargs)` — Regex replace or line-based editing with diff preview.
- `text_replace(path, pattern, replacement, **kwargs)` — Regex replace with diff.
- `move_files(sources, destination)` — Move/rename files.

### Search & Discovery
- `find_for_me(query, **kwargs)` [PRIMARY] — Unified search across filenames, contents, DBs.
- `grep_search(path, pattern, **kwargs)` — Regex file content search.
- `file_search(pattern, **kwargs)` — File search by glob/name.
- `find_line_number_in_file(path, pattern, **kwargs)` — Locate line numbers.
- `search_pending_files(query)` — Search AIHandler pending work files.
- `check_pending_tasks()` — Check pending work orders.

### Database
- `database_query(db, query)` / `database_execute(db, query)` — SELECT / non-SELECT.
- `database_create(db, init_sql)` — Create SQLite DB.
- `database_list_tables(db)` / `database_table_schema(db, table)` — Schema discovery.
- `query_db(query)` / `update_db(query)` — Shorthand for Documentation.db.
- `exec_on_db(query)` — Auto-detect SELECT vs non-SELECT.
- `create_work_order(desc, priority=3, notes, ...)` — Create with duplicate detection.
- `work_order_runner(action, ...)` — Process unfinished work orders.
- `get/set_database_config(key, ...)` — Config table access.

### Web & Network
- `fetch(url, max_length=5000, raw=False)` — Fetch web content.
- `get_web_info(url)` — Page metadata.
- `documentation_search(query, limit=10)` — FTS5 search of Documentation.db.

### System & Command
- `execute_command(command)` / `execute_commands(commands)` — Shell commands (30s timeout).
- `execute_protected(command, timeout=30)` — Host protection layers (13+ layers, RLIMIT, cgroup).
- `execute_python(code, timeout=30, sandbox=True)` — Sandboxed Python execution.
- `set_dry_run(enabled)` / `get_dry_run()` — Toggle dry-run mode.

### Process
- `process_monitor(**filters)` — List/monitor OS processes.
- `process_cleanup(pids=None, kill_zombies=False, ...)` — Kill/stuck process management.

### AI Reasoning
- `sequentialthinking(thought, next, num, total, ...)` [MANDATORY] — THREAD cognitive memory.
- `thinking(content)` — Simple log (NOT a replacement for sequentialthinking).

### Backup
- `backup_audit(**filters)` — Search git-style backup history.
- `backup_undo(entry_id)` — Revert a single mutation.

### Tool Registry
- `register/unregister/list/reload_tool_definitions/sync_tool_registry` — Dynamic tool management.

### Browser Automation
- `get_browser_status()` / `load_url(url)` / `cdp_send(method, params)`

### Subagents
- `subagent_orchestrator(operation, mode, tasks, ...)` — Delegate up to 5 subagent tasks.

### Tape (Append-Only Log)
- `tape_info()` / `tape_search(query, ...)` / `tape_anchors()` / `tape_handoff(name, summary)`

### Skills
- `skill_list()` / `skill_view(name)` / `skill_manage(action, ...)`

### God Watcher
- `god_watcher_admin(action, ...)` — Administer God Watcher protection system.

### GPU Bridge
- `gpu_bridge(action, vectors=None, ...)` — GPU-accelerated THREAD operations.

### DeepSky & VeraCrypt
- `deepsky_admin_monitor(...)` — Monitor ecosystem activity.
- `veracrypt_admin(action, api_key, ...)` — VeraCrypt user/container management.

========================================================================
ERROR HANDLING
========================================================================
1. Always check return values for "Error:" or "Permission denied:" prefixes.
2. Network tools have 10-30 second timeouts.
3. File operations validate paths against allowed directories.
4. read_files reports per-file errors without aborting batch.
5. Database connections auto-close after each operation.
6. When in doubt about dry-run state, call get_dry_run() first.
7. If a tool fails, INVESTIGATE AND FIX IMMEDIATELY (HARD FAIL #2).
"""


class SystemPromptGenerator:
    """
    Generates dynamic system prompts for healing agents.
    
    Reads tool definitions from the FastMCP tool registry and combines
    them with error context, session state, and work order details.
    """

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or get_documentation_db_path()
        self._tool_cache = None
        self._tool_cache_time = 0
        self._cache_ttl = 60  # Re-cache every 60s
    
    async def generate_for_work_order(self, order: Dict) -> str:
        """
        Generate a complete system prompt for a work order.
        """
        tool_defs = self._get_tool_definitions_formatted()
        prompt = BASE_SYSTEM_PROMPT.replace('{TOOL_DEFINITIONS}', tool_defs)
        prompt = prompt.replace('{DOCS_DB_PATH}', self.db_path)
        prompt = prompt.replace('{WORKING_DIR}', os.getcwd())
        
        prompt += f"""

## Work Order Context

**Work Order ID:** #{order.get('id', 'N/A')}
**Priority:** {order.get('priority', 3)}
**Description:** {order.get('description', 'N/A')}
**Created:** {order.get('created_at', 'N/A')}

## Error Details

{self._format_notes_for_prompt(order.get('notes', ''))}

## Session Context

{self._get_session_context()}

## Your Task

{order.get('description', 'Investigate and fix the issue described above.')}

## Completion Requirements

1. ✅ Investigate the root cause of the issue
2. ✅ Implement the fix following all code rules above
3. ✅ Run comprehensive tests using unittest (NOT pytest)
4. ✅ Run full edge case testing
5. ✅ Validate the fix doesn't break existing functionality
6. ✅ Document all changes made
7. ✅ Mark the work order as completed
"""
        return prompt
    
    async def generate_from_error(self, error_context: Dict, tool_defs: List[Dict]) -> str:
        """
        Generate a system prompt directly from an error context.
        """
        tool_str = json.dumps(tool_defs, indent=2) if tool_defs else "Standard tools available."
        prompt = BASE_SYSTEM_PROMPT.replace('{TOOL_DEFINITIONS}', tool_str)
        prompt = prompt.replace('{DOCS_DB_PATH}', self.db_path)
        prompt = prompt.replace('{WORKING_DIR}', os.getcwd())
        
        error_type = error_context.get('type', 'Unknown')
        component = error_context.get('component', 'Unknown')
        summary = error_context.get('summary', 'No summary available.')
        
        prompt += f"""

## Auto-Detected Error Context

**Error Type:** {error_type}
**Component:** {component}
**Summary:** {summary}
**Timestamp:** {error_context.get('timestamp', 'N/A')}

## Stack Trace

```
{error_context.get('stack_trace') or 'No stack trace available.'}
```

## Data Flow Path

{' -> '.join(error_context.get('data_flow_path', ['Unknown']))}

## Session State

{json.dumps(error_context.get('session_state', {}), indent=2)}

## Your Mission

Investigate and fix this error. Follow all rules above.
"""
        return prompt
    
    def _get_tool_definitions_formatted(self) -> str:
        """Get formatted tool definitions from registry."""
        try:
            tools = self._get_tool_registry()
        except Exception as e:
            logger.error(f"_get_tool_definition failed: {e}")
            return ""
        
        if not tools:
            return "Standard MCP tools available (see system prompt for full list)."
        
        categories = {}
        for tool in tools:
            cat = tool.get('category', 'general')
            if cat not in categories:
                categories[cat] = []
            categories[cat].append(tool)
        
        parts = []
        for cat, cat_tools in sorted(categories.items()):
            parts.append(f"\n### {cat.upper()} Tools")
            for t in cat_tools[:10]:
                name = t.get('tool_name', t.get('name', 'unknown'))
                desc = t.get('description', '')
                if desc:
                    parts.append(f"- **{name}**: {desc[:120]}")
                else:
                    parts.append(f"- **{name}**")
        
        return '\n'.join(parts)
    
    def _get_tool_registry(self) -> List[Dict]:
        """Read tool registry from database with caching."""
        import time
        now = time.time()
        
        if self._tool_cache and (now - self._tool_cache_time) < self._cache_ttl:
            return self._tool_cache
        
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            try:
                cursor.execute('''
                    SELECT tool_name, description, category FROM tool_registry 
                    WHERE enabled = 1
                    ORDER BY category, tool_name
                    LIMIT 50
                ''')
                tools = []
                for row in cursor.fetchall():
                    tools.append({
                        'tool_name': row[0],
                        'description': row[1] or '',
                        'category': row[2] or 'general'
                    })
                self._tool_cache = tools
                self._tool_cache_time = now
                conn.close()
                return tools
            except sqlite3.OperationalError:
                conn.close()
                return []
        except Exception as e:
            logger.debug(f"Could not read tool registry: {e}")
            return []
    
    def _format_notes_for_prompt(self, notes: str) -> str:
        """Format work order notes for inclusion in system prompt."""
        if not notes:
            return "No additional error details available."
        if len(notes) > 4000:
            notes = notes[:4000] + "\n\n... (truncated)"
        return notes
    
    def _get_session_context(self) -> str:
        """Get current session context."""
        return (
            "Session persistence is active. All state is checkpointed to SQLite.\n"
            "If this session is interrupted, it will resume from last checkpoint.\n"
            "The FastMCP server is available via Phooks bridge for tool calls.\n"
            "You have full access to YuniScripts ecosystem including file operations,\n"
            "database queries, and code editing."
        )
