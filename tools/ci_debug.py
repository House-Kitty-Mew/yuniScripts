#!/usr/bin/env python3
"""
ci_debug.py — Local CI Debug Runner for AI Agents

Runs GitHub Actions workflows locally to provide fast feedback without
pushing to GitHub.  Wraps `act` (Docker-based workflow runner) and
`gha-debug` (lightweight syntax checker) behind a unified interface.

Features:
  - Validate workflow YAML syntax
  - Run full workflows or specific jobs locally
  - Parse test output into structured JSON for AI consumption
  - Track pass/fail counts per job
  - Auto-install missing dependencies
  - Dry-run mode for introspection

Usage as FastMCP tool:
    ci_debug(action="validate", workflow=".github/workflows/test.yml")
    ci_debug(action="run-job", workflow=".github/workflows/test.yml", job="test")
    ci_debug(action="run-batch", workflow=".github/workflows/test.yml", batch=1)

Usage as standalone script:
    python tools/ci_debug.py validate .github/workflows/test.yml
    python tools/ci_debug.py run-job .github/workflows/test.yml test
    python tools/ci_debug.py run-all .github/workflows/test.yml
    python tools/ci_debug.py list-jobs .github/workflows/test.yml
    python tools/ci_debug.py list-batches
"""

import sys
import os
import json
import re
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Optional, Any, List, Dict

# ── Project root detection ─────────────────────────────────────────────

_PROJECT_ROOT: Path = None  # type: ignore[assignment]


def _get_project_root() -> Path:
    """Find the project root (where .github/workflows/ lives)."""
    global _PROJECT_ROOT
    if _PROJECT_ROOT is not None:
        return _PROJECT_ROOT

    # Try from cwd
    cwd = Path(os.getcwd()).resolve()
    for parent in [cwd] + list(cwd.parents):
        if (parent / ".github" / "workflows").exists():
            _PROJECT_ROOT = parent
            return parent

    # Try from script location
    script_dir = Path(__file__).resolve().parent.parent
    if (script_dir / ".github" / "workflows").exists():
        _PROJECT_ROOT = script_dir
        return script_dir

    _PROJECT_ROOT = cwd
    return cwd


# ── Dependency management ──────────────────────────────────────────────

_REQUIRED_PACKAGES = {
    "act-bin": "act — local GitHub Actions runner",
    "gha-debug": "gha-debug — lightweight workflow debugger",
}

_INSTALLED_CACHE: set = set()


def _check_deps(quiet: bool = False) -> List[str]:
    """Check which required packages are installed. Returns missing names."""
    missing = []
    for pkg in _REQUIRED_PACKAGES:
        try:
            result = subprocess.run(
                [sys.executable, "-m", "pip", "show", pkg.replace("-", "_")],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode != 0:
                missing.append(pkg)
            elif not quiet:
                _INSTALLED_CACHE.add(pkg)
        except (subprocess.TimeoutExpired, OSError):
            missing.append(pkg)
    return missing


def _install_deps(missing: List[str], dry_run: bool = False) -> str:
    """Install missing dependencies. Returns status."""
    if not missing:
        return "All dependencies already installed."

    if dry_run:
        pkgs = ", ".join(missing)
        return f"[DRY-RUN] Would install: pip install {pkgs}"

    parts = []
    for pkg in missing:
        try:
            result = subprocess.run(
                [sys.executable, "-m", "pip", "install", pkg],
                capture_output=True, text=True, timeout=60
            )
            if result.returncode == 0:
                _INSTALLED_CACHE.add(pkg)
                parts.append(f"✅ {pkg}: installed")
            else:
                stderr = result.stderr.strip()[:200]
                parts.append(f"❌ {pkg}: install failed — {stderr}")
        except subprocess.TimeoutExpired:
            parts.append(f"⏱ {pkg}: install timed out")
        except OSError as e:
            parts.append(f"❌ {pkg}: {e}")

    return "; ".join(parts)


# ── Workflow discovery ─────────────────────────────────────────────────

def _resolve_workflow(workflow: str) -> Path:
    """Resolve a workflow path relative to the project root.

    Args:
        workflow: Absolute path, relative "test.yml", or short name "test"

    Returns:
        Resolved Path to the workflow file.

    Raises:
        FileNotFoundError if the file doesn't exist.
    """
    p = Path(workflow)
    if p.is_absolute() and p.exists():
        return p

    proot = _get_project_root()
    workflows_dir = proot / ".github" / "workflows"

    # Try as-is relative to project root
    candidate = proot / p
    if candidate.exists():
        return candidate

    # Try relative to workflows dir
    candidate = workflows_dir / p
    if candidate.exists():
        return candidate

    # Try with .yml extension
    if not p.suffix:
        for ext in (".yml", ".yaml"):
            candidate = workflows_dir / f"{p}{ext}"
            if candidate.exists():
                return candidate

    # Try with workflow name only
    for ext in (".yml", ".yaml"):
        candidate = workflows_dir / f"{p.name}{ext}" if p.suffix else workflows_dir / f"{p}{ext}"
        if candidate.exists():
            return candidate

    raise FileNotFoundError(
        f"Workflow '{workflow}' not found. "
        f"Looked in: {workflows_dir}, {proot / p}, {candidate}"
    )


def _find_batches() -> List[Dict]:
    """Discover test batches from the project's run_all_tests.py, if it exists."""
    proot = _get_project_root()
    runner_path = proot / "tests" / "run_all_tests.py"
    if not runner_path.exists():
        return []

    # Parse the BATCHES variable from run_all_tests.py via simple regex
    try:
        content = runner_path.read_text(encoding="utf-8")
        # Try to import and extract the BATCHES value
        import importlib.util as _util
        spec = _util.spec_from_file_location("_batch_loader", str(runner_path))
        if spec and spec.loader:
            mod = _util.module_from_spec(spec)
            # Don't execute — just extract BATCHES via AST
            import ast
            tree = ast.parse(content, str(runner_path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Assign):
                    for target in node.targets:
                        if isinstance(target, ast.Name) and target.id == "BATCHES":
                            # Evaluate the AST node safely
                            code = ast.unparse(node.value)
                            namespace = {}
                            exec(code, {"Path": Path, "os": os}, namespace)
                            # BATCHES was assigned, and exec may have put it in namespace
                            # Actually let me just eval the value
                            batches = eval(code, {"Path": Path})
                            return batches
    except Exception:
        pass

    return []


# ── Core operations ────────────────────────────────────────────────────

def validate_workflow(workflow: str, dry_run: bool = False) -> str:
    """Validate a workflow YAML file using gha-debug (preferred) or yamllint.

    Args:
        workflow: Path or name of the workflow file.
        dry_run: If True, only show what would be checked.

    Returns:
        Structured validation report as JSON string.
    """
    if dry_run:
        return json.dumps({
            "status": "dry-run",
            "message": f"Would validate: {workflow}",
        })

    result: Dict[str, Any] = {
        "workflow": workflow,
        "valid": False,
        "errors": [],
        "warnings": [],
        "tools_used": [],
    }

    try:
        wf_path = _resolve_workflow(workflow)
        result["workflow"] = str(wf_path)
        result["valid"] = True  # Assume valid until proven otherwise
    except FileNotFoundError as e:
        result["errors"].append(str(e))
        return json.dumps(result, indent=2)

    # Method 1: gha-debug (lightweight syntax checker)
    try:
        r = subprocess.run(
            ["gha-debug", "run", str(wf_path)],
            capture_output=True, text=True, timeout=30
        )
        result["tools_used"].append("gha-debug")
        if r.returncode != 0:
            result["valid"] = False
            result["errors"].append(r.stderr[:500] if r.stderr else r.stdout[:500])
        if r.stdout:
            result["warnings"].extend(
                line.strip() for line in r.stdout.split("\n")
                if "warning" in line.lower() or "WARN" in line
            )
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        result["warnings"].append(f"gha-debug not available or timed out: {e}")

    # Method 2: yamllint fallback
    try:
        r = subprocess.run(
            ["yamllint", str(wf_path)],
            capture_output=True, text=True, timeout=10
        )
        result["tools_used"].append("yamllint")
        if r.returncode != 0:
            result["valid"] = False
            for line in r.stdout.split("\n"):
                if line.strip():
                    result["errors"].append(line.strip())
    except (FileNotFoundError, subprocess.TimeoutExpired):
        result["warnings"].append("yamllint not available — syntax check limited")

    # Method 3: Built-in YAML validation (always available)
    try:
        import yaml
        with open(wf_path) as f:
            yaml.safe_load(f)
        result["tools_used"].append("pyyaml")
    except ImportError:
        result["warnings"].append("PyYAML not installed — skipping YAML parse check")
    except yaml.YAMLError as e:
        result["valid"] = False
        result["errors"].append(f"YAML syntax error: {e}")

    if result["valid"]:
        result["summary"] = "✅ Workflow syntax is valid."
    else:
        result["summary"] = f"❌ Found {len(result['errors'])} error(s) in workflow."

    return json.dumps(result, indent=2)


def run_workflow_job(workflow: str, job: str = None,
                     event: str = "push", dry_run: bool = False) -> str:
    """Run a specific job from a workflow using act.

    Args:
        workflow: Path or name of the workflow file.
        job: Job name (e.g. "test", "lint"). If None, runs all jobs.
        event: GitHub event to simulate (default: "push").
        dry_run: If True, only show what would run.

    Returns:
        Structured execution report as JSON string.
    """
    result: Dict[str, Any] = {
        "workflow": workflow,
        "job": job or "all",
        "event": event,
        "status": "pending",
        "exit_code": -1,
        "stdout_preview": "",
        "stderr_preview": "",
        "tests_passed": 0,
        "tests_failed": 0,
        "tests_skipped": 0,
        "errors": [],
        "timing_seconds": 0.0,
    }

    try:
        wf_path = _resolve_workflow(workflow)
        result["workflow"] = str(wf_path)
    except FileNotFoundError as e:
        result["status"] = "error"
        result["errors"].append(str(e))
        return json.dumps(result, indent=2)

    if dry_run:
        result["status"] = "dry-run"
        result["stdout_preview"] = f"Would run: act {event} -W {wf_path}"
        if job:
            result["stdout_preview"] += f" --job {job}"
        return json.dumps(result, indent=2)

    # Check deps
    missing = _check_deps(quiet=True)
    if "act-bin" in missing:
        result["errors"].append("act is not installed. Run ci_debug with action='install-deps' first.")
        result["status"] = "error"
        return json.dumps(result, indent=2)

    # Build the act command
    cmd = ["act", event, "-W", str(wf_path)]
    if job:
        cmd.extend(["--job", job])

    # Use --quiet to reduce noise, capture full output
    cmd.append("--quiet")

    try:
        start = time.time()
        r = subprocess.run(
            cmd, capture_output=True, text=True, timeout=600  # 10 min max
        )
        elapsed = time.time() - start
        result["timing_seconds"] = round(elapsed, 1)
        result["exit_code"] = r.returncode

        stdout = r.stdout
        stderr = r.stderr

        # Store previews (truncated for AI consumption)
        result["stdout_preview"] = stdout[-2000:] if len(stdout) > 2000 else stdout
        result["stderr_preview"] = stderr[-1000:] if len(stderr) > 1000 else stderr

        # Parse test results from pytest output
        result["tests_passed"], result["tests_failed"], result["tests_skipped"] = \
            _parse_test_counts(stdout + stderr)

        # Parse PASS/FAIL lines from custom inline test framework
        inline_pass, inline_fail, inline_skip = _parse_inline_test_counts(stdout + stderr)
        result["tests_passed"] += inline_pass
        result["tests_failed"] += inline_fail
        result["tests_skipped"] += inline_skip

        if r.returncode == 0:
            result["status"] = "passed"
        else:
            result["status"] = "failed"
            # Extract failure summaries
            result["errors"] = _extract_failures(stdout + stderr)

    except subprocess.TimeoutExpired:
        result["status"] = "timed-out"
        result["errors"].append("Workflow execution exceeded 600 second timeout.")
    except FileNotFoundError as e:
        result["status"] = "error"
        result["errors"].append(f"act binary not found: {e}")
    except Exception as e:
        result["status"] = "error"
        result["errors"].append(str(e))

    return json.dumps(result, indent=2)


def run_batch(batch_index: int, dry_run: bool = False) -> str:
    """Run a specific test batch from the project's run_all_tests.py.

    Args:
        batch_index: 1-based batch number (e.g. 1 for Batch 1: Engine Core).
        dry_run: If True, only show what would run.

    Returns:
        Structured batch execution report as JSON string.
    """
    result: Dict[str, Any] = {
        "batch_index": batch_index,
        "batch_name": "",
        "status": "pending",
        "exit_code": -1,
        "tests_passed": 0,
        "tests_failed": 0,
        "tests_skipped": 0,
        "errors": [],
        "timing_seconds": 0.0,
    }

    batches = _find_batches()
    if not batches:
        result["status"] = "error"
        result["errors"].append(
            "No test batches found. Is tests/run_all_tests.py available?"
        )
        return json.dumps(result, indent=2)

    if batch_index < 1 or batch_index > len(batches):
        result["status"] = "error"
        result["errors"].append(
            f"Batch index {batch_index} out of range. "
            f"Available: 1-{len(batches)}"
        )
        return json.dumps(result, indent=2)

    batch = batches[batch_index - 1]
    result["batch_name"] = batch["name"]
    modules = batch.get("modules", [])

    if not modules:
        result["status"] = "error"
        result["errors"].append(f"Batch {batch_index} has no modules.")
        return json.dumps(result, indent=2)

    if dry_run:
        result["status"] = "dry-run"
        result["stdout_preview"] = (
            f"Would run batch {batch_index}: {batch['name']}\n"
            f"  python -m unittest -v {' '.join(modules)}"
        )
        return json.dumps(result, indent=2)

    # Run each module in the batch via unittest
    proot = _get_project_root()
    cmd = [sys.executable, "-m", "unittest", "-v"] + modules

    try:
        start = time.time()
        r = subprocess.run(
            cmd, capture_output=True, text=True,
            cwd=str(proot), timeout=180
        )
        elapsed = time.time() - start
        result["timing_seconds"] = round(elapsed, 1)
        result["exit_code"] = r.returncode

        output = r.stdout + r.stderr
        result["stdout_preview"] = output[-2000:] if len(output) > 2000 else output

        # Parse unittest output
        pass_count, fail_count = _parse_unittest_counts(output)
        result["tests_passed"] = pass_count
        result["tests_failed"] = fail_count

        # Also parse inline test framework counts
        inline_pass, inline_fail, inline_skip = _parse_inline_test_counts(output)
        result["tests_passed"] += inline_pass
        result["tests_failed"] += inline_fail
        result["tests_skipped"] += inline_skip

        if r.returncode == 0:
            result["status"] = "passed"
        else:
            result["status"] = "failed"
            result["errors"] = _extract_failures(output)

    except subprocess.TimeoutExpired:
        result["status"] = "timed-out"
        result["errors"].append("Batch execution exceeded 180 second timeout.")
    except Exception as e:
        result["status"] = "error"
        result["errors"].append(str(e))

    return json.dumps(result, indent=2)


def run_all(dry_run: bool = False) -> str:
    """Run ALL test batches sequentially. Returns aggregated results.

    Args:
        dry_run: If True, only show what would run.

    Returns:
        Aggregated execution report as JSON string.
    """
    batches = _find_batches()
    if not batches:
        return json.dumps({
            "status": "error",
            "errors": ["No test batches found in tests/run_all_tests.py"],
        })

    if dry_run:
        return json.dumps({
            "status": "dry-run",
            "batches": [
                {"index": i + 1, "name": b["name"], "modules": b.get("modules", [])}
                for i, b in enumerate(batches)
            ],
        })

    results = []
    total_passed = 0
    total_failed = 0
    total_skipped = 0
    total_time = 0.0

    for i in range(len(batches)):
        batch_result = json.loads(run_batch(i + 1, dry_run=False))
        results.append(batch_result)
        total_passed += batch_result.get("tests_passed", 0)
        total_failed += batch_result.get("tests_failed", 0)
        total_skipped += batch_result.get("tests_skipped", 0)
        total_time += batch_result.get("timing_seconds", 0.0)

    return json.dumps({
        "status": "completed",
        "total_tests_passed": total_passed,
        "total_tests_failed": total_failed,
        "total_tests_skipped": total_skipped,
        "total_time_seconds": round(total_time, 1),
        "batches_run": len(results),
        "overall": "passed" if total_failed == 0 else "failed",
        "batch_results": results,
    }, indent=2)


def list_workflows(dry_run: bool = False) -> str:
    """List all available workflow files.

    Args:
        dry_run: If True, only show scan path.

    Returns:
        JSON array of workflow file info.
    """
    proot = _get_project_root()
    workflows_dir = proot / ".github" / "workflows"

    if not workflows_dir.exists():
        return json.dumps({
            "status": "error",
            "errors": [f"No .github/workflows/ directory found at {workflows_dir}"],
        })

    workflows = []
    for f in sorted(workflows_dir.glob("*.yml")) + sorted(workflows_dir.glob("*.yaml")):
        rel = f.relative_to(proot)
        size = f.stat().st_size
        workflows.append({
            "path": str(rel),
            "name": f.stem,
            "size_bytes": size,
        })

    return json.dumps({
        "status": "ok" if workflows else "empty",
        "workflows_dir": str(workflows_dir.relative_to(proot)),
        "count": len(workflows),
        "workflows": workflows,
    }, indent=2)


def list_jobs(workflow: str, dry_run: bool = False) -> str:
    """List all jobs in a workflow by parsing the YAML.

    Args:
        workflow: Path or name of the workflow file.
        dry_run: If True, only resolve the path.

    Returns:
        JSON array of job names with metadata.
    """
    try:
        wf_path = _resolve_workflow(workflow)
    except FileNotFoundError as e:
        return json.dumps({"status": "error", "errors": [str(e)]})

    if dry_run:
        return json.dumps({
            "status": "dry-run",
            "workflow": str(wf_path),
        })

    try:
        import yaml
        with open(wf_path) as f:
            data = yaml.safe_load(f)
    except Exception as e:
        return json.dumps({"status": "error", "errors": [f"Failed to parse YAML: {e}"]})

    jobs = data.get("jobs", {})
    result = []
    for job_name, job_data in jobs.items():
        result.append({
            "name": job_name,
            "runs_on": job_data.get("runs-on", "unknown"),
            "strategy": job_data.get("strategy", {}),
            "steps": len(job_data.get("steps", [])),
        })

    return json.dumps({
        "status": "ok",
        "workflow": str(wf_path.relative_to(_get_project_root())),
        "count": len(result),
        "jobs": result,
    }, indent=2)


def list_batches(dry_run: bool = False) -> str:
    """List all available test batches from run_all_tests.py.

    Args:
        dry_run: If True, only show source path.

    Returns:
        JSON array of batch info.
    """
    proot = _get_project_root()
    runner_path = proot / "tests" / "run_all_tests.py"

    if dry_run:
        return json.dumps({
            "status": "dry-run",
            "source": str(runner_path.relative_to(proot)),
        })

    batches = _find_batches()
    if not batches:
        return json.dumps({
            "status": "error",
            "errors": [
                f"tests/run_all_tests.py not found at {runner_path}",
                "Run with action='run-batch' and specify modules directly."
            ],
        })

    result = []
    for i, batch in enumerate(batches):
        result.append({
            "index": i + 1,
            "name": batch["name"],
            "module_count": len(batch.get("modules", [])),
            "modules": batch.get("modules", []),
        })

    return json.dumps({
        "status": "ok",
        "count": len(result),
        "batches": result,
    }, indent=2)


def install_deps(dry_run: bool = False) -> str:
    """Install required dependencies (act-bin, gha-debug, pyyaml).

    Args:
        dry_run: If True, only show what would be installed.

    Returns:
        Installation result as JSON string.
    """
    if dry_run:
        return json.dumps({
            "status": "dry-run",
            "to_install": list(_REQUIRED_PACKAGES.keys()),
        })

    missing = _check_deps()
    result = _install_deps(missing, dry_run=False)

    # Also try to install PyYAML for workflow parsing
    try:
        import yaml
    except ImportError:
        try:
            subprocess.run(
                [sys.executable, "-m", "pip", "install", "pyyaml"],
                capture_output=True, text=True, timeout=30
            )
            result += "; pyyaml: installed"
        except Exception:
            result += "; pyyaml: skipped (can still use gha-debug)"

    report = {
        "status": "completed",
        "installed": list(_INSTALLED_CACHE),
        "details": result,
    }
    return json.dumps(report, indent=2)


# ── Output parsing helpers ─────────────────────────────────────────────

def _parse_test_counts(output: str) -> tuple:
    """Parse pytest output for test counts.

    Returns:
        (passed, failed, skipped) tuple.
    """
    passed = failed = skipped = 0

    # pytest summary: "= 10 passed, 2 failed, 1 skipped in 0.45s ="
    m = re.search(r"=+\s*(\d+)\s+passed", output)
    if m:
        passed = int(m.group(1))

    m = re.search(r"(\d+)\s+failed", output)
    if m:
        failed = int(m.group(1))

    m = re.search(r"(\d+)\s+skipped", output)
    if m:
        skipped = int(m.group(1))

    return passed, failed, skipped


def _parse_inline_test_counts(output: str) -> tuple:
    """Parse custom inline test framework output (PASS/FAIL/SKIP).

    Returns:
        (passed, failed, skipped) tuple.
    """
    passed = len(re.findall(r"^  \033\[92mPASS\033\[0m", output, re.MULTILINE))
    failed = len(re.findall(r"^  \033\[91mFAIL\033\[0m", output, re.MULTILINE))
    skipped = len(re.findall(r"^  \033\[93mSKIP\033\[0m", output, re.MULTILINE))
    return passed, failed, skipped


def _parse_unittest_counts(output: str) -> tuple:
    """Parse unittest output for test counts.

    Returns:
        (passed, failed) tuple.
    """
    passed = failed = 0

    # "Ran 42 tests" then "OK" or "FAILED (failures=2)"
    m = re.search(r"Ran (\d+) tests?", output)
    if m:
        total = int(m.group(1))
        m_f = re.search(r"failures=(\d+)", output)
        m_e = re.search(r"errors=(\d+)", output)
        f_count = int(m_f.group(1)) if m_f else 0
        e_count = int(m_e.group(1)) if m_e else 0
        failed = f_count + e_count
        passed = total - failed

    return passed, failed


def _extract_failures(output: str) -> List[str]:
    """Extract failure messages from test output.

    Args:
        output: Combined stdout+stderr from test run.

    Returns:
        List of failure description strings.
    """
    failures = []

    # pytest FAILURE lines
    for m in re.finditer(r"FAILED (.+)", output):
        failures.append(m.group(1).strip())

    # unittest failure lines
    for m in re.finditer(r"FAIL: (.+)", output):
        failures.append(m.group(1).strip())

    # Custom framework FAIL lines (colored)
    for m in re.finditer(r"FAIL\s{2}(.+)", output):
        failures.append(m.group(1).strip())

    # AssertionError details
    for m in re.finditer(r"AssertionError:?\s*(.*)", output):
        detail = m.group(1).strip()
        if detail and detail not in failures:
            failures.append(f"AssertionError: {detail[:200]}")

    # Error lines from pytest
    for m in re.finditer(r"ERRORS\s*$.*?(?=^-{3,}|^=)", output, re.DOTALL):
        failures.append("Pytest errors section — see full output")

    return failures[:20]  # Limit to 20 failures


# ══════════════════════════════════════════════════════════════════════
# FastMCP Tool Entry Point
# ══════════════════════════════════════════════════════════════════════

def ci_debug(action: str = "list-workflows",
             workflow: str = None,
             job: str = None,
             event: str = "push",
             batch: int = None,
             dry_run: bool = False) -> str:
    """


    Debug and run GitHub Actions workflows locally for fast CI feedback.

    **When to use:** Use this tool to test GitHub Actions workflows
    without pushing to GitHub.  It wraps `act` (Docker-based runner)
    and `gha-debug` (lightweight syntax checker) behind a single
    interface designed for AI agent consumption.

    **Actions:**
      - ``validate`` — Validate workflow YAML syntax.
      - ``run-job`` — Run a single job from a workflow.
      - ``run-batch`` — Run a numbered test batch from run_all_tests.py.
      - ``run-all`` — Run ALL test batches sequentially.
      - ``list-workflows`` — List all available workflow files.
      - ``list-jobs`` — List jobs in a specific workflow.
      - ``list-batches`` — List all test batches from run_all_tests.py.
      - ``install-deps`` — Install act-bin, gha-debug, pyyaml.

    **Args:**
      - ``action`` — One of the actions listed above (default: list-workflows).
      - ``workflow`` — Workflow path, filename, or short name (required for validate, run-job, list-jobs).
      - ``job`` — Job name to run (optional for run-job; runs all jobs if omitted).
      - ``event`` — GitHub event to simulate (default: "push").
      - ``batch`` — Batch number 1-N (required for run-batch).
      - ``dry_run`` — If True, preview without executing (default: False).

    **Returns:**
      Structured JSON string with results, exit codes, test counts, and
      failure summaries designed for AI agent consumption.

    **Error handling:**
      - Returns JSON with ``status: "error"`` and human-readable errors.
      - Missing dependencies are reported; use ``action="install-deps"``.
      - Timeout after 10 minutes for full workflow runs.

    **Examples:**
      >>> ci_debug("validate", ".github/workflows/test.yml")
      >>> ci_debug("run-job", "test.yml", job="test")
      >>> ci_debug("run-batch", batch=1)
      >>> ci_debug("list-workflows")
      >>> ci_debug("install-deps")
    """
    actions = {
        "validate": lambda: validate_workflow(workflow, dry_run),
        "run-job": lambda: run_workflow_job(workflow, job, event, dry_run),
        "run-batch": lambda: run_batch(batch, dry_run),
        "run-all": lambda: run_all(dry_run),
        "list-workflows": lambda: list_workflows(dry_run),
        "list-jobs": lambda: list_jobs(workflow, dry_run),
        "list-batches": lambda: list_batches(dry_run),
        "install-deps": lambda: install_deps(dry_run),
    }

    handler = actions.get(action)
    if handler is None:
        return json.dumps({
            "status": "error",
            "action": action,
            "valid_actions": list(actions.keys()),
            "error": f"Unknown action '{action}'. Valid: {', '.join(actions.keys())}",
        })

    return handler()


# ══════════════════════════════════════════════════════════════════════
# CLI Entry Point
# ══════════════════════════════════════════════════════════════════════

def _cli() -> None:
    """Command-line interface for the CI debug tool."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Local CI Debug Runner — run GitHub Actions workflows locally.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s validate test.yml
  %(prog)s run-job test.yml test
  %(prog)s run-batch 1
  %(prog)s run-all
  %(prog)s list-workflows
  %(prog)s list-jobs test.yml
  %(prog)s list-batches
  %(prog)s install-deps
        """,
    )

    parser.add_argument(
        "action",
        choices=["validate", "run-job", "run-batch", "run-all",
                 "list-workflows", "list-jobs", "list-batches", "install-deps"],
        help="Action to perform",
    )
    parser.add_argument("workflow", nargs="?",
                        help="Workflow file path or name (for validate, run-job, list-jobs)")
    parser.add_argument("--job", "-j", default=None,
                        help="Job name to run (for run-job)")
    parser.add_argument("--event", "-e", default="push",
                        help="GitHub event to simulate (default: push)")
    parser.add_argument("--batch", "-b", type=int, default=None,
                        help="Batch number (for run-batch)")
    parser.add_argument("--dry-run", "-n", action="store_true",
                        help="Preview without executing")

    args = parser.parse_args()

    # Route to the correct function
    result = ci_debug(
        action=args.action,
        workflow=args.workflow,
        job=args.job,
        event=args.event,
        batch=args.batch,
        dry_run=args.dry_run,
    )

    print(result)


if __name__ == "__main__":
    _cli()
