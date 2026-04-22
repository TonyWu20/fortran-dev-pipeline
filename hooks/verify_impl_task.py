#!/usr/bin/env python3
"""
SubagentStop hook for implementation-executor subagents.

Reads the sidecar JSON written by `task-sidecar.sh prepare`, runs acceptance
commands, updates the checkpoint file, appends to the execution report, and
stages + commits all changes.  Returns a structured JSON reason on stdout so
the main orchestrating agent gets ground-truth verification results.

Triggered by: SubagentStop with matcher "implementation-executor"

Exit codes:
  0  — always (let the subagent stop); results are communicated via stdout JSON.
"""

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# ── Constants ────────────────────────────────────────────────────────────────

PROJECT_DIR = os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd())
SIDECAR_DIR = Path(PROJECT_DIR) / ".claude" / "hooks"
REPORTS_DIR = Path(PROJECT_DIR) / "execution_reports"
STALE_THRESHOLD_SECONDS = 30 * 60  # 30 minutes

# Regex to extract a task ID from free text (covers TASK-N, Issue-N, Fix-N, etc.)
_TASK_ID_RE = re.compile(r"\b(TASK-\d+|Issue-\d+|Fix-\d+)\b", re.IGNORECASE)


# ── Helpers ──────────────────────────────────────────────────────────────────


def read_stdin() -> dict:
    """Read the SubagentStop JSON input from stdin."""
    try:
        return json.loads(sys.stdin.read())
    except (json.JSONDecodeError, EOFError):
        return {}


def _is_stale(data: dict) -> bool:
    """Return True if the sidecar timestamp is older than STALE_THRESHOLD_SECONDS."""
    ts = data.get("timestamp", "")
    if not ts:
        return False
    try:
        written = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        age = (datetime.now(timezone.utc) - written).total_seconds()
        return age > STALE_THRESHOLD_SECONDS
    except ValueError:
        return False


def read_sidecar(path: Path) -> dict | None:
    """Read the sidecar JSON at *path*.  Returns None if missing, unreadable, or stale."""
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    if _is_stale(data):
        return None
    return data


def resolve_sidecar(hook_input: dict) -> tuple[Path | None, dict | None]:
    """Find and read the sidecar file for the stopped subagent.

    Resolution strategy (in order):
    1. Parse task ID from last_assistant_message → look for current_task_{ID}.json
    2. Glob fallback: scan SIDECAR_DIR for current_task_*.json, pick newest by mtime
    3. Legacy fallback: current_task.json (old single-file format)

    Returns (sidecar_path, sidecar_data) or (None, None).
    """
    SIDECAR_DIR.mkdir(parents=True, exist_ok=True)

    # 1. Try to extract the task ID from the subagent's last message
    last_msg = hook_input.get("last_assistant_message", "")
    if last_msg:
        m = _TASK_ID_RE.search(last_msg)
        if m:
            task_id = m.group(1).upper()
            candidate = SIDECAR_DIR / f"current_task_{task_id}.json"
            data = read_sidecar(candidate)
            if data is not None:
                return candidate, data

    # 2. Glob fallback — pick the freshest non-stale sidecar
    candidates = sorted(
        SIDECAR_DIR.glob("current_task_*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for candidate in candidates:
        data = read_sidecar(candidate)
        if data is not None:
            return candidate, data

    # 3. Legacy fallback for old single-file format
    legacy = SIDECAR_DIR / "current_task.json"
    data = read_sidecar(legacy)
    if data is not None:
        return legacy, data

    return None, None


def run_command(cmd: str, cwd: str) -> dict:
    """Run a shell command, return {command, exit_code, stdout, stderr}."""
    try:
        result = subprocess.run(
            cmd,
            shell=True,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=120,
        )
        return {
            "command": cmd,
            "exit_code": result.returncode,
            "stdout": result.stdout[-2000:] if len(result.stdout) > 2000 else result.stdout,
            "stderr": result.stderr[-2000:] if len(result.stderr) > 2000 else result.stderr,
        }
    except subprocess.TimeoutExpired:
        return {
            "command": cmd,
            "exit_code": -1,
            "stdout": "",
            "stderr": "Command timed out after 120 seconds",
        }
    except Exception as e:
        return {
            "command": cmd,
            "exit_code": -1,
            "stdout": "",
            "stderr": str(e),
        }


# ── Checkpoint ───────────────────────────────────────────────────────────────


def checkpoint_path(plan_slug: str) -> Path:
    return REPORTS_DIR / f".checkpoint_{plan_slug}.json"


def read_checkpoint(plan_slug: str) -> dict:
    cp = checkpoint_path(plan_slug)
    if cp.exists():
        try:
            return json.loads(cp.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {"plan": "", "base_commit": "", "completed": [], "failed": [], "blocked": []}


def write_checkpoint(plan_slug: str, data: dict) -> None:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    cp = checkpoint_path(plan_slug)
    cp.write_text(json.dumps(data, indent=2) + "\n")


# ── Execution Report ─────────────────────────────────────────────────────────


def report_path(plan_slug: str) -> Path:
    date_str = datetime.now().strftime("%Y%m%d")
    return REPORTS_DIR / f"execution_{plan_slug}_{date_str}.md"


def ensure_report_header(rpath: Path, plan_path: str) -> None:
    """Create the report file with a header if it doesn't exist yet."""
    if rpath.exists():
        return
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    header = (
        f"# Execution Report\n\n"
        f"**Plan**: {plan_path}\n"
        f"**Started**: {now}\n"
        f"**Status**: In Progress\n\n"
        f"## Task Results\n\n"
    )
    rpath.write_text(header)


def append_task_result(
    rpath: Path,
    task_id: str,
    task_desc: str,
    passed: bool,
    results: list[dict],
    prose: list[str],
) -> None:
    """Append a task result section to the execution report."""
    status = "✓ Passed" if passed else "✗ Failed"
    lines = [
        f"### {task_id}: {task_desc}\n",
        f"- **Status**: {status}\n",
        f"- **Validation output**:\n",
    ]
    for r in results:
        exit_label = "PASSED" if r["exit_code"] == 0 else f"FAILED (exit {r['exit_code']})"
        lines.append(f"  - `{r['command']}`: {exit_label}\n")
        output = (r["stdout"] + r["stderr"]).strip()
        if output and not passed:
            # Only include full output on failures to keep report concise
            lines.append(f"    ```\n")
            for line in output.splitlines()[:30]:
                lines.append(f"    {line}\n")
            lines.append(f"    ```\n")
    if prose:
        lines.append(f"- **Prose criteria (manual check needed)**:\n")
        for p in prose:
            lines.append(f"  - {p}\n")
    lines.append("\n")
    with open(rpath, "a") as f:
        f.writelines(lines)


# ── Git ──────────────────────────────────────────────────────────────────────


def git_stage_and_commit(task_id: str, task_desc: str, plan_slug: str) -> str | None:
    """Stage all changes and commit.  Returns commit hash or None."""
    try:
        # Stage all modified and new files
        subprocess.run(
            ["git", "add", "-A"],
            cwd=PROJECT_DIR,
            capture_output=True,
            timeout=30,
        )
        # Check if there's anything to commit
        status = subprocess.run(
            ["git", "diff", "--cached", "--quiet"],
            cwd=PROJECT_DIR,
            capture_output=True,
            timeout=10,
        )
        if status.returncode == 0:
            return None  # Nothing staged

        msg = f"feat({plan_slug}): {task_id}: {task_desc}"
        result = subprocess.run(
            ["git", "commit", "-m", msg],
            cwd=PROJECT_DIR,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode == 0:
            # Extract short hash
            hash_result = subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                cwd=PROJECT_DIR,
                capture_output=True,
                text=True,
                timeout=10,
            )
            return hash_result.stdout.strip()
    except Exception:
        pass
    return None


# ── Main ─────────────────────────────────────────────────────────────────────


def main() -> None:
    hook_input = read_stdin()
    sidecar_path, sidecar = resolve_sidecar(hook_input)

    if sidecar is None:
        # No sidecar — not an implementation-executor run, or all candidates stale
        sys.exit(0)

    task_id = sidecar["task_id"]
    task_desc = sidecar.get("task_description", "")
    plan_path = sidecar.get("plan_path", "")
    plan_slug = sidecar.get("plan_slug", "unknown")
    acceptance_commands = sidecar.get("acceptance_commands", [])
    acceptance_prose = sidecar.get("acceptance_prose", [])
    all_task_ids = sidecar.get("all_task_ids", [])

    # ── Step B: Run acceptance commands ──────────────────────────────────────

    results = []
    all_passed = True
    for cmd in acceptance_commands:
        r = run_command(cmd, PROJECT_DIR)
        results.append(r)
        if r["exit_code"] != 0:
            all_passed = False

    task_status = "passed" if all_passed else "failed"

    # ── Step C: Update checkpoint ────────────────────────────────────────────

    cp = read_checkpoint(plan_slug)
    cp["plan"] = plan_path

    # Capture base commit once (before this task's commit) so the skill can
    # later run `git diff <base_commit> HEAD` to get the full-round diff.
    if not cp.get("base_commit"):
        try:
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=PROJECT_DIR,
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                cp["base_commit"] = result.stdout.strip()
        except Exception:
            pass

    # Prune stale task IDs not in the current plan's manifest
    if all_task_ids:
        valid = set(all_task_ids)
        for lst in ("completed", "failed", "blocked"):
            cp[lst] = [t for t in cp[lst] if t in valid]

    # Remove current task from any existing list to avoid duplicates
    for lst in ("completed", "failed", "blocked"):
        cp[lst] = [t for t in cp[lst] if t != task_id]
    # Add to the right list
    if task_status == "passed":
        cp["completed"].append(task_id)
    else:
        cp["failed"].append(task_id)
    write_checkpoint(plan_slug, cp)

    # ── Step D: Append to execution report ───────────────────────────────────

    rpath = report_path(plan_slug)
    ensure_report_header(rpath, plan_path)
    append_task_result(rpath, task_id, task_desc, all_passed, results, acceptance_prose)

    # Clean up sidecar before staging so it is never committed and leaves no
    # unstaged deletion in the working tree after the commit.
    try:
        sidecar_path.unlink()
    except OSError:
        pass

    # ── Step E: Git stage & commit ───────────────────────────────────────────

    commit_hash = git_stage_and_commit(task_id, task_desc, plan_slug)

    # ── Step F: Return results to main agent ─────────────────────────────────

    lines = [f"Hook verified {task_id}:"]
    if not acceptance_commands:
        lines.append("  (no acceptance commands found — task checkpointed without verification)")
    for r in results:
        label = "PASSED" if r["exit_code"] == 0 else f"FAILED (exit {r['exit_code']})"
        lines.append(f"  - {r['command']}: {label}")
        if r["exit_code"] != 0:
            # Include brief error output
            err = (r["stderr"] or r["stdout"]).strip().splitlines()
            for e in err[:5]:
                lines.append(f"    {e}")

    lines.append(f"Checkpoint updated: {task_id} marked {task_status}.")

    if commit_hash:
        lines.append(f"Committed as {commit_hash}.")
    else:
        lines.append("No new changes to commit.")

    base_commit = cp.get("base_commit", "")
    if base_commit:
        lines.append(f"Base commit (round start): {base_commit}.")

    if acceptance_prose:
        lines.append("Prose criteria (manual check needed):")
        for p in acceptance_prose:
            lines.append(f"  - {p}")

    if all_passed:
        lines.append("\nProceed to next task.")
    else:
        lines.append(f"\n{task_id} FAILED verification. Retry or mark as failed.")

    # Output JSON for the main agent
    output = {"reason": "\n".join(lines)}
    print(json.dumps(output))
    sys.exit(0)


if __name__ == "__main__":
    main()
