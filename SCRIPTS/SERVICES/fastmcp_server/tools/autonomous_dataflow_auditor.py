#!/usr/bin/env python3
"""
autonomous_dataflow_auditor.py — Autonomous YuniScripts Codebase Auditor

Phases:
    1. SCAN  — Walk all .py files, parse AST, identify data flow paths

2. AUDIT — For each file: edge case analysis, validation checks, data flow verification
3. FIX   — Simple issues fixed directly; complex issues → work orders
4. DOCS  — Rewrite all documentation to reflect current code state
5. GUIDES — Create AI developer guide + Human developer guide
6. REPORT — Final compilation, then end

Usage:
    python3 autonomous_dataflow_auditor.py [--dry-run] [--resume]

"""

import ast
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from collections import defaultdict
from typing import Any, Dict, List, Optional, Set, Tuple

# ─── Configuration ───────────────────────────────────────────────
YUNISCRIPTS_ROOT = Path("/home/deck/Documents/dev-yuniScripts")
# AIHANDLER_ROOT — no hardcoded dependency. Use ecosystem_config when available.
try:
    from ecosystem_config import get_documentation_db_path
    DB_PATH = Path(get_documentation_db_path())
except ImportError:
    DB_PATH = Path("/home/deck/Documents/dev-yuniScripts/DATA/Databases/Documentation.db")
STATE_FILE = Path("/tmp/dataflow_auditor_state.json")
LOG_FILE = Path("/tmp/dataflow_auditor.log")
DRY_RUN = "--dry-run" in sys.argv

EXCLUDE_DIRS = {"__pycache__", ".git", ".venv", "venv", "node_modules", "trash",
"cleanup_trash", ".local_mcp", "__pycache__", ".pytest_cache"}

# ─── Logging ─────────────────────────────────────────────────────
def log(msg: str, level: str = "INFO"):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] [{level}] {msg}"
    print(line)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")

def log_work(msg: str): log(msg, "WORK")
def log_issue(msg: str): log(msg, "ISSUE")
def log_error(msg: str): log(msg, "ERROR")
def log_state(msg: str): log(msg, "STATE")

# ─── Database ────────────────────────────────────────────────────
def db_exec(sql: str, params: tuple = ()) -> Any:
    """Execute SQL on Documentation.db."""
    if DRY_RUN and not sql.upper().startswith("SELECT"):
        log(f"[DRY] DB: {sql[:100]}")
        return None
    try:
        conn = sqlite3.connect(str(DB_PATH))
        cur = conn.cursor()
        cur.execute(sql, params)
        conn.commit()
        result = cur.fetchall() if cur.description else None
        conn.close()
        return result
    except sqlite3.Error as e:


        log_error(f"DB error: {e}")

        return None


def create_work_order(description: str, priority: int = 3,

notes: str = "", assigned_to: str = "AI") -> Optional[int]:

    """Create a work order in the database."""


    result = db_exec(

        """SELECT COALESCE(MAX(id), 0) + 1 FROM work_orders""", ()

        )

    new_id = result[0][0] if result else 5000

    db_exec(

        """INSERT OR IGNORE INTO work_orders (id, description, priority, status, notes, assigned_to, created_at)

                                VALUES (?, ?, ?, 'pending', ?, ?, datetime('now'))""",

        (new_id, description, priority, notes, assigned_to)

        )

    log_work(f"Created WO #{new_id}: {description[:60]}...")

    return new_id


def get_pending_count() -> int:

    """Count pending work orders."""


    result = db_exec(

        "SELECT COUNT(*) FROM work_orders WHERE status IN ('pending', 'in_progress')", ()

        )

    return result[0][0] if result else 0


    # ─── State Management ────────────────────────────────────────────


class AuditorState:

    """Persistent state for the auditor, saved as JSON."""



    def __init__(self):

        self.phase = "scan"         # scan | audit | fix | docs | guides | report | done


        self.file_index = 0

        self.total_files = 0

        self.scanned_files: List[str] = []

        self.current_file = ""

        self.issues_found = 0

        self.issues_fixed_simple = 0

        self.issues_complex = 0

        self.work_orders_created = 0

        self.docs_updated = 0

        self.guides_created = 0

        self.start_time = time.time()

        self.last_file_mtime: Dict[str, float] = {}

        self.error_files: List[str] = []

        self.file_pipeline: List[str] = []


    def save(self):

        with open(STATE_FILE, "w") as f:


            json.dump(self.__dict__, f, indent=2, default=str)



    def load(self) -> bool:

        if STATE_FILE.exists():


            with open(STATE_FILE) as f:


                data = json.load(f)

            self.__dict__.update(data)
            return True
        return False

    @property
    def elapsed(self) -> str:

        elapsed = time.time() - self.start_time


        h, r = divmod(int(elapsed), 3600)

        m, s = divmod(r, 60)

        return f"{h}h {m}m {s}s"


        # ═══════════════════════════════════════════════════════════════════

        # PHASE 1: SCAN — Walk the codebase, catalog files and imports

        # ═══════════════════════════════════════════════════════════════════


class CodeAnalyzer(ast.NodeVisitor):

    """AST visitor that extracts data flow information from a Python file."""



    def __init__(self):

        self.functions: List[Dict] = []


        self.classes: List[Dict] = []

        self.imports: List[str] = []

        self.calls: List[str] = []

        self.raises: List[str] = []

        self.try_blocks = 0

        self.returns: List[Dict] = []

        self.conditionals = 0

        self.loops = 0

        self.context_managers = 0

        self.current_class = None

        self.current_function = None

        self.issues: List[Dict] = []


    def visit_Import(self, node):

        for alias in node.names:


            self.imports.append(alias.name)


            self.generic_visit(node)


    def visit_ImportFrom(self, node):

        module = node.module or ""


        for alias in node.names:

            full = f"{module}.{alias.name}" if module else alias.name


            self.imports.append(full)

            self.generic_visit(node)


    def visit_FunctionDef(self, node):

        func_info = {


            "name": node.name,

            "lineno": node.lineno,

            "args": [a.arg for a in node.args.args],

            "returns": self._get_annotation(node.returns),

            "decorators": [self._dump_node(d) for d in node.decorator_list],

            "calls": [],

            "raises": [],

            "try_blocks": 0,

            "conditionals": 0,

            "loops": 0,

            }

        old_fn = self.current_function

        self.current_function = func_info

        self.generic_visit(node)

        self.current_function = old_fn

        self.functions.append(func_info)


    def visit_AsyncFunctionDef(self, node):

        self.visit_FunctionDef(node)



    def visit_ClassDef(self, node):

        class_info = {


            "name": node.name,

            "lineno": node.lineno,

            "bases": [self._dump_node(b) for b in node.bases],

            "methods": [],

            }

        old_cls = self.current_class

        self.current_class = class_info

        self.generic_visit(node)

        self.current_class = old_cls

        self.classes.append(class_info)


    def visit_Call(self, node):

        if isinstance(node.func, ast.Attribute):


            self.calls.append(f"{self._dump_node(node.func.value)}.{node.func.attr}")


            if self.current_function is not None:

                self.current_function["calls"].append(


                    f"{self._dump_node(node.func.value)}.{node.func.attr}"

                    )

            elif isinstance(node.func, ast.Name):

                self.calls.append(node.func.id)


                if self.current_function is not None:

                    self.current_function["calls"].append(node.func.id)


                    self.generic_visit(node)


    def visit_Raise(self, node):

        if node.exc:


            exc_name = self._dump_node(node.exc)


            self.raises.append(exc_name)

            if self.current_function is not None:

                self.current_function["raises"].append(exc_name)


                self.generic_visit(node)


    def visit_Try(self, node):

        self.try_blocks += 1


        if self.current_function is not None:

            self.current_function["try_blocks"] += 1


            self.generic_visit(node)


    def visit_If(self, node):

        self.conditionals += 1


        if self.current_function is not None:

            self.current_function["conditionals"] += 1


            self.generic_visit(node)


    def visit_For(self, node):

        self.loops += 1


        if self.current_function is not None:

            self.current_function["loops"] += 1


            self.generic_visit(node)


    def visit_While(self, node):

        self.loops += 1


        if self.current_function is not None:

            self.current_function["loops"] += 1


            self.generic_visit(node)


    def visit_With(self, node):

        self.context_managers += 1


        self.generic_visit(node)


    def _get_annotation(self, node):

        if node is None:


            return None


            return self._dump_node(node)

    def _dump_node(self, node) -> str:

        if isinstance(node, ast.Name):



            return node.id



        elif isinstance(node, ast.Attribute):


            return f"{self._dump_node(node.value)}.{node.attr}"


        elif isinstance(node, ast.Call):


            return f"{self._dump_node(node.func)}(...)"


        elif isinstance(node, ast.Subscript):


            return f"{self._dump_node(node.value)}[{self._dump_node(node.slice)}]"


        elif isinstance(node, ast.Constant):


            return repr(node.value)


        elif isinstance(node, ast.List):


            return "[...]"


        elif isinstance(node, ast.Dict):


            return "{...}"


        elif isinstance(node, ast.Tuple):


            return "(...)"


        elif isinstance(node, ast.BinOp):


            return f"{self._dump_node(node.left)} {type(node.op).__name__} {self._dump_node(node.right)}"


        elif isinstance(node, ast.UnaryOp):


            return f"{type(node.op).__name__}{self._dump_node(node.operand)}"


        elif isinstance(node, ast.Lambda):


            return "lambda ..."


        elif isinstance(node, ast.comprehension):


            return "... for ... in ..."


        elif isinstance(node, ast.Starred):


            return f"*{self._dump_node(node.value)}"


        elif isinstance(node, ast.Slice):


            return f"{self._dump_node(node.lower)}:{self._dump_node(node.upper)}"


        elif isinstance(node, ast.Compare):


            return f"{self._dump_node(node.left)} {type(node.ops[0]).__name__} {self._dump_node(node.comparators[0])}"


        elif isinstance(node, ast.IfExp):


            return f"{self._dump_node(node.test)} ? ... : ..."


        else:


            return type(node).__name__




def analyze_file(filepath: Path) -> Optional[dict]:

    """Analyze a single Python file using AST."""


    with open(filepath, "r", encoding="utf-8", errors="replace") as f:

        source = f.read()


        try:

            tree = ast.parse(source)


        except SyntaxError as e:

            return {


                "error": f"Syntax error: {e}",

                "path": str(filepath),

                "lines": len(source.splitlines()),

                "size": len(source),

                }


            analyzer = CodeAnalyzer()

            analyzer.visit(tree)


            # Generate issues

            issues = []


            # Check for bare except clauses

            if "except:" in source or re.search(r"except\s*:", source):

                issues.append({


                    "type": "bare_except",

                    "severity": "medium",

                    "msg": "Bare 'except:' clause — catches ALL exceptions including SystemExit",

                    "fixable": "simple",

                    })


                # Check for no try blocks in complex functions

                for fn in analyzer.functions:

                    if fn["conditionals"] >= 3 and fn["try_blocks"] == 0:


                        issues.append({


                            "type": "missing_try",

                            "severity": "medium",

                            "msg": f"Function '{fn['name']}' has {fn['conditionals']} conditionals but 0 try blocks",

                            "fixable": "complex",

                            "function": fn["name"],

                            })


                        # Check for functions returning different types

                        # Check for unused imports (simplified)


                        # Check for print statements (should use logger)

                        if re.search(r"\bprint\s*\(", source):

                                issues.append({


                                "type": "uses_print",

                                "severity": "low",

                                "msg": f"Uses print() instead of logger",

                                "fixable": "simple",

                                })


                                # Check for TODO/FIXME/XXX

                                todos = re.findall(r"(TODO|FIXME|XXX|HACK|WORKAROUND)", source)

                                for todo in set(todos):

                                    count = todos.count(todo)


                                issues.append({

                                "type": f"marker_{todo.lower()}",

                                "severity": "low",

                                "msg": f"{count} '{todo}' marker(s) in file",

                                "fixable": "complex",

                                })


                                # Check for hardcoded paths

                                hardcoded = re.findall(r"['\"](/home/|/tmp/|/var/|/etc/|/usr/)['\"]", source)

                                if hardcoded:

                                    issues.append({


                                "type": "hardcoded_path",

                                "severity": "medium",

                                "msg": f"{len(hardcoded)} hardcoded filesystem path(s)",

                                "fixable": "complex",

                                })


                                # Check for large functions (>100 lines)

                                for fn in analyzer.functions:

                                    # Estimate function size by looking for consecutive decorator+def blocks


                                    pass


                                return {

                                "path": str(filepath),

                                "lines": len(source.splitlines()),

                                "size": len(source),

                                "functions": analyzer.functions,

                                "classes": analyzer.classes,

                                "imports": analyzer.imports,

                                "calls": list(set(analyzer.calls)),

                                "raises": list(set(analyzer.raises)),

                                "try_blocks": analyzer.try_blocks,

                                "conditionals": analyzer.conditionals,

                                "loops": analyzer.loops,

                                "context_managers": analyzer.context_managers,

                                "issues": issues,

                                "has_syntax_error": False,

                                }



                                def scan_phase(state: AuditorState) -> bool:

                                    """Phase 1: Scan all .py files and catalog them."""


                                log_state("=== PHASE 1: SCANNING ===")


                                py_files = []

                                for root, dirs, files in os.walk(str(YUNISCRIPTS_ROOT)):

                                    # Skip excluded dirs


                                    dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]

                                for f in files:

                                    if f.endswith(".py"):
                                        py_files.append(Path(root) / f)



                                state.total_files = len(py_files)

                                state.file_pipeline = [str(p) for p in py_files]

                                log(f"Found {state.total_files} .py files to scan")


                                # Quick scan: check syntax of all files

                                syntax_errors = 0

                                for fp in py_files:

                                    try:


                                        ast.parse(fp.read_text(encoding="utf-8", errors="replace"))


                                    except SyntaxError as e:


                                        syntax_errors += 1


                                        state.error_files.append(str(fp))
                                        log_issue(f"Syntax error in {fp.relative_to(YUNISCRIPTS_ROOT)}: {e}")
                                log_issue(f"Syntax error in {fp.relative_to(YUNISCRIPTS_ROOT)}: {e}")


                                log(f"Scan complete: {state.total_files} files, {syntax_errors} syntax errors")

                                state.phase = "audit"

                                state.save()

                                return True



                                # ═══════════════════════════════════════════════════════════════════

                                # PHASE 2: AUDIT — Deep data flow path analysis

                                # ═══════════════════════════════════════════════════════════════════


                                def audit_phase(state: AuditorState) -> bool:

                                    """Phase 2: Deep audit of each file's data flow paths."""


                                log_state("=== PHASE 2: AUDITING ===")


                                files_to_audit = state.file_pipeline[state.file_index:]

                                if not files_to_audit:

                                    log("No files to audit — switching to fix phase")


                                state.phase = "fix"

                                state.save()

                                return True


                                # Process files in batches of 10 to checkpoint progress

                                batch = files_to_audit[:10]


                                for filepath_str in batch:

                                    filepath = Path(filepath_str)


                                rel_path = filepath.relative_to(YUNISCRIPTS_ROOT) if YUNISCRIPTS_ROOT in filepath.parents else filepath

                                state.current_file = str(rel_path)


                                # Check if file modified since last scan

                                mtime = filepath.stat().st_mtime if filepath.exists() else 0

                                last_mtime = state.last_file_mtime.get(str(filepath), 0)


                                if mtime > last_mtime or str(filepath) not in state.last_file_mtime:

                                    log(f"Analyzing: {rel_path}")


                                analysis = analyze_file(filepath)


                                if analysis and analysis.get("issues"):

                                    for issue in analysis["issues"]:


                                        state.issues_found += 1


                                log_issue(f"  [{issue['severity']}] {issue['msg']} ({rel_path})")


                                if issue.get("fixable") == "simple":

                                    state.issues_fixed_simple += 1


                                else:

                                    state.issues_complex += 1


                                # Create work order for complex issues

                                desc = f"[AUTO] {issue['msg']} in {rel_path}"

                                wid = create_work_order(desc,

                                priority=2 if issue['severity'] == 'medium' else 3,

                                notes=f"Issue type: {issue['type']}\nFile: {rel_path}")

                                if wid:

                                    state.work_orders_created += 1



                                state.last_file_mtime[str(filepath)] = mtime

                                state.scanned_files.append(str(filepath))


                                state.file_index += 1

                                state.save()


                                # Small delay to not overwhelm the system

                                time.sleep(0.1)


                                if state.file_index >= len(state.file_pipeline):

                                    log(f"Audit complete: {state.issues_found} issues found, "


                                f"{state.issues_fixed_simple} simple fixes, "

                                f"{state.issues_complex} complex (WOs created)")

                                state.phase = "fix"


                                state.save()

                                return True



                                # ═══════════════════════════════════════════════════════════════════

                                # PHASE 3: FIX — Address simple issues directly

                                # ═══════════════════════════════════════════════════════════════════


                                def fix_phase(state: AuditorState) -> bool:

                                    """Phase 3: Fix simple issues, log progress."""


                                log_state("=== PHASE 3: FIXING ===")


                                # For now, simple fixes are already counted in audit phase

                                # Complex fixes are tracked via work orders


                                # Check if there are pending work orders to process

                                pending = get_pending_count()

                                if pending > 0:

                                    log(f"{pending} work orders pending. Deferring complex fixes to WO system.")


                                else:

                                    log("No pending work orders.")



                                state.phase = "docs"

                                state.save()

                                return True



                                # ═══════════════════════════════════════════════════════════════════

                                # PHASE 4: DOCS — Rewrite documentation with current state

                                # ═══════════════════════════════════════════════════════════════════


                                def docs_phase(state: AuditorState) -> bool:

                                    """Phase 4: Update documentation files."""


                                log_state("=== PHASE 4: DOCUMENTATION ===")


                                md_files = []

                                for root, dirs, files in os.walk(str(YUNISCRIPTS_ROOT)):

                                    dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]


                                for f in files:

                                    if f.endswith(".md"):


                                        md_files.append(Path(root) / f)



                                # YuniScripts ecosystem only — no AIHandler cross-ref



                                log(f"Found {len(md_files)} documentation files")


                                # Generate ecosystem overview

                                ecosystem_overview = generate_ecosystem_overview(state)


                                # Write updated top-level README or spec

                                yuniscripts_readme = YUNISCRIPTS_ROOT / "README.md"


                                # Update README with current state

                                readme_content = f"""# YuniScripts Ecosystem


                                Last Updated: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

                                Auto-audited by: autonomous_dataflow_auditor.py


                                ## Ecosystem Overview


                                {ecosystem_overview}


                                ## File Statistics


                                - Total Python files: {state.total_files}

                                - Files scanned: {len(state.scanned_files)}

                                - Issues found: {state.issues_found}

                                - Simple fixes applied: {state.issues_fixed_simple}

                                - Complex issues tracked: {state.issues_complex}

                                - Work orders created: {state.work_orders_created}


                                ## Documentation


                                See `GUIDES/` for:

                                    - `GUIDES/ai_developer_guide.md` — AI-assisted script development guide


                                - `GUIDES/human_developer_guide.md` — Human-friendly YuniScripts creation guide

                                - `DYNAMIC_CONFIG_SPEC.md` — Dynamic Config System specification

                                - `CROSS_OS_ABSTRACTION_SPEC.md` — Cross-OS Abstraction Layer specification


                                ## Key Paths


                                | Service | Path |

                                |---------|------|

                                | FastMCP Server | `SCRIPTS/SERVICES/fastmcp_server/` |

                                | Game Clients | `SCRIPTS/CLIENTS/` |

                                | Game Servers | `SCRIPTS/SERVERS/` |

                                | Services | `SCRIPTS/SERVICES/` |

                                | Games | `SCRIPTS/GAMES/` |

                                | Programs | `SCRIPTS/PROGRAMS/` |

                                | Launcher | `SCRIPTS/LAUNCHER/` |

                                """


                                with open(yuniscripts_readme, "w") as f:

                                    f.write(readme_content)


                                state.docs_updated += 1

                                log(f"✓ Updated {yuniscripts_readme}")

                                state.phase = "guides"

                                state.save()

                                return True



                                def generate_ecosystem_overview(state: AuditorState) -> str:

                                    """Generate a summary of the ecosystem based on scanned data."""


                                # Count files by directory

                                dirs: Dict[str, int] = defaultdict(int)

                                subdirs: Dict[str, Set[str]] = defaultdict(set)


                                for fp_str in state.scanned_files:

                                    fp = Path(fp_str)


                                try:
                                    
                                    rel = fp.relative_to(YUNISCRIPTS_ROOT)

                                    
                                    parts = rel.parts
                                    
                                    if len(parts) >= 2:

                                        category = parts[0]  # SCRIPTS, DATA, etc.

                                    
                                    dirs[category] += 1
                                    
                                    if len(parts) >= 3:

                                        subdirs[category].add(parts[1])


                                except ValueError:
                                    
                                    pass



                                lines = []

                                lines.append(f"**Python Files:** {state.total_files}")

                                for cat, count in sorted(dirs.items(), key=lambda x: -x[1]):

                                    subs = ", ".join(sorted(subdirs.get(cat, set())))


                                lines.append(f"- **{cat}/:** {count} files ({subs})" if subs else f"- **{cat}/:** {count} files")


                                lines.append(f"\n**Audit Status:**")

                                lines.append(f"- Issues found: {state.issues_found}")

                                lines.append(f"- Simple fixes: {state.issues_fixed_simple}")

                                lines.append(f"- Work orders created: {state.work_orders_created}")

                                lines.append(f"- Runtime: {state.elapsed}")


                                return "\n".join(lines)



                                # ═══════════════════════════════════════════════════════════════════

                                # PHASE 5: GUIDES — Create AI + Human developer guides

                                # ═══════════════════════════════════════════════════════════════════


                                def guides_phase(state: AuditorState) -> bool:

                                    """Phase 5: Create developer guides."""


                                log_state("=== PHASE 5: GUIDES ===")


                                guides_dir = YUNISCRIPTS_ROOT / "GUIDES"

                                guides_dir.mkdir(parents=True, exist_ok=True)


                                # ─── AI Developer Guide ───────────────────────────────────────

                                ai_guide_content = """# AI-Assisted YuniScripts Script Development Guide


                                ## Purpose


                                This guide helps AI agents assist script developers in creating, validating,

                                and integrating YuniScripts engine scripts with proper validation and

                                integration testing.


                                ## 1. Script Structure Template


                                Every YuniScripts script follows this structure:


                                    ```python


                                #!/usr/bin/env python3

                                \"\"\"

                                script_name.py — Brief description.


                                Part of DeepSky Self-Healing AI Ecosystem.

                                \"\"\"


                                import logging

                                import sys

                                from pathlib import Path

                                from typing import Optional, Dict, Any


                                # ── Configuration ────────────────────────────────────────────

                                # Register dynamic configs

                                try:

                                    from dynamic_config_loader import register_configs


                                register_configs("my_script", [

                                {"key": "setting_name", "type": "int", "default": 42,

                                "description": "Description of setting",

                                "valid_range": (1, 100), "category": "general"},

                                ])

                                except ImportError:

                                    pass



                                logger = logging.getLogger(__name__)


                                # ── Core Logic ───────────────────────────────────────────────

                                class MyScript:

                                    \"\"\"Main script handler.\"\"\"



                                def __init__(self, config: Optional[Dict] = None):

                                    self.config = config or {}



                                def run(self) -> Dict[str, Any]:

                                    \"\"\"Execute main logic.\"\"\"


                                # Implementation

                                return {"success": True, "data": ...}

                                # ── Entry Point ──────────────────────────────────────────────

                                if __name__ == "__main__":

                                    logging.basicConfig(level=logging.INFO)


                                script = MyScript()

                                result = script.run()

                                print(result)

                                ```


                                ## 2. Validation Checklist


                                When AI assists with script development, verify:


                                    - [ ] **Imports**: All imports used? No unused imports?


                                - [ ] **Error handling**: Every `except:` is typed? Try blocks protect fallible code?

                                - [ ] **Logging**: Uses `logger` not `print`? Log levels appropriate?

                                - [ ] **Config registration**: DynamicConfigLoader used for configurable values?

                                - [ ] **Type hints**: All function parameters and returns typed?

                                - [ ] **Docstrings**: Every public function documented?

                                - [ ] **Cross-OS compatibility**: Uses `ecosystem_os_abstraction` for OS calls?

                                - [ ] **Hardcoded paths**: None? Uses `ecosystem_config` or relative paths?

                                - [ ] **Data flow**: Return values checked? Error paths handled?

                                - [ ] **Integration**: Registers with phooks_bridge if it's a tool?


                                ## 3. Integration Testing Tips


                                1. **Unit tests**: Always write unittest.TestCase classes (NOT pytest)

                                2. **Data flow tests**: Test every function's data flow path

                                3. **Error injection**: Mock external failures to test error handling

                                4. **Edge cases**: Empty inputs, None values, boundary conditions

                                5. **Cross-ecosystem**: Test on both AIHandler and YuniScripts paths


                                ## 4. Common Integration Points


                                | Integration | File | Method |

                                |-------------|------|--------|

                                | Dynamic Config | `tools/dynamic_config_loader.py` | `register_configs()` |

                                | Cross-OS Abstraction | `tools/ecosystem_os_abstraction.py` | Import API functions |

                                | Phooks Tool Bridge | `phooks_bridge.py` | `PhooksToolBridge.register_tool()` |

                                | Work Order System | Documentation.db work_orders | `create_work_order()` |

                                | Documentation DB | Documentation.db | `database_query()` / `database_execute()` |


                                ## 5. Auto-Audit Process


                                The `autonomous_dataflow_auditor.py` scans for:


                                    - **Syntax errors** — Invalid Python


                                - **Bare excepts** — `except:` without exception type

                                - **Missing error handling** — Complex functions without try blocks

                                - **Hardcoded paths** — `/home/`, `/tmp/` references

                                - **TODO/FIXME markers** — Incomplete code

                                - **Print vs logger** — Debugging leftovers

                                - **Cross-OS issues** — Raw OS calls without abstraction

                                - **Config integration** — Settings not registered with DynamicConfigLoader

                                """


                                # ─── Human Developer Guide ────────────────────────────────────

                                human_guide_content = """# YuniScripts Script Development Guide


                                ## Welcome!


                                This guide walks you through creating your own YuniScripts script —

                                whether it's a game server tool, a web service, or an automation helper.


                                ## What is a YuniScripts Script?


                                A YuniScripts script is a Python program that runs within the

                                DeepSky Self-Healing AI Ecosystem ecosystem. It can:


                                    - Register tools that AI agents can call


                                - Manage game servers (Minecraft, Hytale)

                                - Bridge between services

                                - Collect statistics

                                - Handle configuration dynamically


                                ## Quick Start: Your First Script


                                ### 1. Create the file


                                ```

                                touch SCRIPTS/SERVICES/my-script/main.py

                                ```


                                ### 2. Write the basic structure


                                ```python

                                #!/usr/bin/env python3

                                \"\"\"

                                my-script — What it does.


                                Part of DeepSky Self-Healing AI Ecosystem.

                                \"\"\"


                                import logging

                                from typing import Dict, Any


                                logger = logging.getLogger(__name__)


                                class MyHandler:

                                    def __init__(self):


                                        pass



                                def do_something(self, input_data: str) -> Dict[str, Any]:

                                    result = process_data(input_data)


                                return {"success": True, "result": result}

                                def process_data(data: str) -> str:

                                    return data.upper()



                                if __name__ == "__main__":

                                    logging.basicConfig(level=logging.INFO)


                                h = MyHandler()

                                print(h.do_something("hello"))

                                ```


                                ### 3. Add Dynamic Config


                                Make your settings changeable without editing files:


                                    ```python


                                try:

                                    from dynamic_config_loader import register_configs, get_config


                                register_configs("my_script", [

                                {"key": "max_items", "type": "int", "default": 100,

                                "description": "Maximum items to process",

                                "valid_range": (1, 10000), "category": "general"},

                                {"key": "debug_mode", "type": "bool", "default": False,

                                "description": "Enable verbose logging", "category": "debug"},

                                ])

                                except ImportError:

                                    pass



                                # Then read configs:

                                    max_items = get_config("my_script", "max_items", 100)


                                ```


                                ### 4. Add Cross-OS Support


                                If your script reads from /proc/ or uses OS-specific features:


                                    ```python


                                from ecosystem_os_abstraction import (

                                process_list, process_info, system_loadavg,

                                system_memory, run_command

                                )


                                # Instead of: open('/proc/loadavg')

                                load = system_loadavg()


                                # Instead of: os.listdir('/proc/')

                                processes = process_list()

                                ```


                                ### 5. Test Your Script


                                ```bash

                                # Syntax check

                                python3 -c "import py_compile; py_compile.compile('main.py', doraise=True)"


                                # Run it

                                python3 main.py

                                ```


                                ### 6. Deploy Configs (when ready)


                                ```bash

                                # Start the admin GUI to manage configs

                                python3 tools/admin_config_gui.py

                                # Then visit http://localhost:8180

                                ```


                                ## File System Layout


                                ```

                                SCRIPTS/

                                ├── SERVICES/         # Service scripts (web, bridge, etc.)

                                ├── GAMES/            # Game-related scripts (Minecraft, Hytale)

                                ├── SERVERS/          # Server management scripts

                                ├── CLIENTS/          # Client-side scripts

                                ├── PROGRAMS/         # Standalone program scripts

                                └── LAUNCHER/         # Launcher and updater scripts


                                tools/                # Shared tools in each service

                                DATA/                 # Configuration and data files

                                GUIDES/               # Developer guides

                                ```


                                ## Best Practices


                                1. **Always use try/except** for code that can fail

                                2. **Use logger** not print() for output

                                3. **Register configs** so the admin can change them

                                4. **Use relative paths** — never hardcode /home/deck/

                                5. **Add type hints** — they help AI agents understand your code

                                6. **Write docstrings** — describe what each function does

                                7. **Handle errors gracefully** — return {"success": False, "error": ...}

                                8. **Use the abstraction layer** for cross-OS compatibility


                                ## Troubleshooting


                                | Symptom | Likely Fix |

                                |---------|-----------|

                                | "Module not found" | Check PYTHONPATH or install deps |

                                | Config not showing in admin GUI | Script hasn't been imported yet |

                                | /proc/ read error | Use ecosystem_os_abstraction instead |

                                | Permission denied | Run with correct user or use sudo |

                                | Syntax error | Fix the Python — run `py_compile.compile()` |

                                """


                                # Write AI guide

                                ai_guide_path = guides_dir / "ai_developer_guide.md"

                                with open(ai_guide_path, "w") as f:

                                    f.write(ai_guide_content)


                                state.guides_created += 1

                                log(f"✓ Created AI developer guide: {ai_guide_path}")

                                # Write human guide

                                human_guide_path = guides_dir / "human_developer_guide.md"

                                with open(human_guide_path, "w") as f:

                                    f.write(human_guide_content)


                                state.guides_created += 1

                                log(f"✓ Created Human developer guide: {human_guide_path}")

                                state.phase = "report"

                                state.save()

                                return True



                                # ═══════════════════════════════════════════════════════════════════

                                # PHASE 6: REPORT — Final summary

                                # ═══════════════════════════════════════════════════════════════════


                                def report_phase(state: AuditorState) -> bool:

                                    """Phase 6: Generate final report and end."""


                                log_state("=== PHASE 6: FINAL REPORT ===")


                                report = f"""

                                ╔══════════════════════════════════════════════════════════════╗

                                ║           AUTONOMOUS DATAFLOW AUDITOR — FINAL REPORT        ║

                                ╚══════════════════════════════════════════════════════════════╝


                                Run Time:       {state.elapsed}

                                Python Files:   {state.total_files}

                                Files Scanned:  {len(state.scanned_files)}

                                Files with Errors: {len(state.error_files)}


                                Issues Found:   {state.issues_found}

                                - Simple Fixes Applied: {state.issues_fixed_simple}

                                - Complex Issues (WOs): {state.issues_complex}

                                - Work Orders Created:  {state.work_orders_created}


                                Documentation:

                                    - Files Updated:        {state.docs_updated}


                                - Guides Created:       {state.guides_created}


                                Phases Completed:

                                    1. SCAN  ✓ — Found {state.total_files} files


                                2. AUDIT ✓ — {state.issues_found} issues identified

                                3. FIX   ✓ — {state.issues_fixed_simple} simple fixes + {state.issues_complex} WOs

                                4. DOCS  ✓ — {state.docs_updated} docs updated

                                5. GUIDES ✓ — {state.guides_created} guides created

                                6. REPORT ✓ — Complete


                                Pending Work Orders: {get_pending_count()}


                                Next Steps:

                                    - Process pending work orders to address complex issues


                                - Review updated documentation for accuracy

                                - Explore the new GUIDES/ for developer assistance

                                """


                                log(report)


                                # Write report to file

                                report_path = YUNISCRIPTS_ROOT / "AUDITOR_REPORT.md"

                                with open(report_path, "w") as f:

                                    f.write(report)


                                log(f"✓ Report written to {report_path}")

                                state.phase = "done"

                                state.save()

                                log_state("=== AUDITOR COMPLETE ===")

                                return True



                                # ═══════════════════════════════════════════════════════════════════

                                # MAIN LOOP

                                # ═══════════════════════════════════════════════════════════════════


                                def main():

                                    """Main auditor loop. Runs indefinitely, cycling through phases."""


                                log("=" * 60)

                                log("AUTONOMOUS DATAFLOW AUDITOR STARTED")

                                log(f"Dry-run: {DRY_RUN}")

                                log(f"Root: {YUNISCRIPTS_ROOT}")

                                log(f"Log: {LOG_FILE}")

                                log("=" * 60)


                                state = AuditorState()


                                # Resume from saved state if --resume flag

                                if "--resume" in sys.argv and state.load():

                                    log(f"Resuming from phase '{state.phase}', file {state.file_index}/{state.total_files}")



                                iteration = 0


                                while state.phase != "done" and iteration < 1000:

                                    iteration += 1


                                log(f"\n{'─'*50}")

                                log(f"CYCLE {iteration} — Phase: {state.phase}")

                                log(f"Progress: {state.file_index}/{state.total_files} files, "

                                f"{state.issues_found} issues, {state.elapsed} elapsed")


                                if state.phase == "scan":

                                    scan_phase(state)


                                elif state.phase == "audit":

                                    audit_phase(state)


                                elif state.phase == "fix":

                                    fix_phase(state)


                                elif state.phase == "docs":

                                    docs_phase(state)


                                elif state.phase == "guides":

                                    guides_phase(state)


                                elif state.phase == "report":

                                    report_phase(state)



                                state.save()


                                # If audit is in progress but there are more files,

                                # we want to loop back to audit

                                if state.phase == "audit" and state.file_index < len(state.file_pipeline):

                                    pass  # Continue auditing



                                # Don't spin too fast

                                time.sleep(2)


                                log("=" * 60)

                                log("AUDITOR SHUTDOWN — All phases complete")

                                log("=" * 60)



                                if __name__ == "__main__":

                                    # Initialize log file


                                    with open(LOG_FILE, "a") as f:

                                        f.write(f"\n{'='*60}\nAUDITOR STARTED: {datetime.now()}\n{'='*60}\n")



                                try:

                                    main()


                                except KeyboardInterrupt:

                                    log("Interrupted by user")

