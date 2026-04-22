---
name: implementation-executor
description: Strictly execute detailed implementation plans without modification or questioning. Use when the user says "/implementation-executor <plan-path>", "execute the plan in <file>", "implement according to <plan>", "follow the implementation plan", or mentions running a PHASE implementation plan. This skill mechanically follows structured plans with TASK-N sections and validation commands. ALWAYS use this skill when a plan file path is given — never attempt inline execution.
---

# Implementation Executor

> **Quick-start checklist** (read before doing anything else):
>
> 1. Read the plan file first, then check for a checkpoint file (see Checkpoint / Resume). If found, skip completed tasks — but still delegate every remaining task to a subagent. Do NOT execute tasks inline.
> 2. For every task: use `subagent_type: "fortran-development-pipeline:implementation-executor"` in the Agent call — never the default agent.
> 3. After all tasks: write the execution report to `execution_reports/`.
> 4. After the report: create a git commit with all modified files.

You are executing a detailed implementation plan. Your role is strictly mechanical: read the plan, execute each task exactly as specified, validate, and commit. Do not question, interpret, or modify anything.

## Invocation

The user will invoke this as `/implementation-executor <plan-path>`. Read that plan file, then launch the `implementation-executor` agent to work through it task by task.

## Plan Structure

Implementation plans are TOML files following the compilable-plan-spec:

```toml
[meta]
title = "Phase X.Y: <Phase Name>"
source_branch = "branch-name"
created = "YYYY-MM-DD"

[dependencies]
# task_id = ["dep1", "dep2"]  — omit if all tasks are independent
# TASK-3 = ["TASK-1", "TASK-2"]  — TASK-3 depends on 1 and 2

[tasks.TASK-1]
description = "Task description"
type = "replace"   # "replace" | "create" | "delete"
acceptance = [
    "gfortran -c -Wall src/module.f90",
    "make test",
]

[[tasks.TASK-1.changes]]
file = "path/to/file.f90"
before = '''
exact content to match
'''
after = '''
replacement content
'''

[tasks.TASK-2]
description = "Create new file"
type = "create"
acceptance = ["test -f path/to/new_module.f90"]

[[tasks.TASK-2.changes]]
file = "path/to/new_module.f90"
after = '''
file content here
'''
```

## LSP-First Approach

Before making any code changes, leverage LSP tools for understanding and validation:

- **Before editing**: Use LSP to understand existing code structure, find subroutine/module definitions, check types
- **During implementation**: Use LSP diagnostics to catch errors early, before running gfortran
- **For refactoring**: Use LSP rename operations instead of manual find-replace
- **For validation**: Check LSP diagnostics first, then run acceptance commands

**Key LSP workflows**:

1. **Understanding code**: `LSP hover`, `LSP definition`, `LSP references` to understand what exists
2. **Finding symbols**: `LSP documentSymbol` to locate subroutines/modules/types before editing
3. **Validation**: `LSP diagnostics` to check for errors immediately after edits
4. **Refactoring**: `LSP rename` for symbol renames, `LSP references` to find all usages

This approach catches issues faster and produces more reliable code than edit-then-compile cycles.

- **Re-locate before every edit in a multi-edit task**: Prior edits shift line numbers. Before each edit, re-query LSP `documentSymbol` or `Grep` for a unique surrounding string to find the current location. Never rely on a line number from the plan document.

### Pre-flight: Ensure Compiled Scripts Exist

Before launching any task agents, the orchestrator **must** ensure compiled scripts are available:

1. Look for `<plan-dir>/compiled/manifest.json` (sibling to the plan file).
2. If the `compiled/` directory or `manifest.json` does not exist, run the `/compile-plan` skill on the plan file first to generate them. This always succeeds with full task coverage.
3. Once `manifest.json` exists, read it. Every task will have a compiled script at `compiled/<TASK-ID>.sh`.

This ensures compiled scripts are always available before any task agent is launched.

## Task Sidecar Script

This plugin includes a shared `scripts/task-sidecar.sh` (at the plugin root) for enumerating tasks and preparing verification sidecars. It reads from the compiled `manifest.json` — the single source of truth for task IDs, descriptions, and acceptance commands.

```bash
# List all task IDs from the compiled manifest
bash <plugin-root>/scripts/task-sidecar.sh list <plan-dir>/compiled/manifest.json

# Create sidecar file for hook-automated verification
bash <plugin-root>/scripts/task-sidecar.sh prepare <plan-dir>/compiled/manifest.json <TASK-ID>
```

**Resolve `<plugin-root>` before running any sidecar command.** Run the following command once and record the printed path — use it as the literal value everywhere `<plugin-root>` appears in this section:

```bash
python3 -c "
import json; from pathlib import Path
p = Path.home() / '.claude/plugins/installed_plugins.json'
data = json.loads(p.read_text())
for key in ['fortran-development-pipeline@my-claude-marketplace', 'fortran-development-pipeline@local']:
    if key in data['plugins']:
        print(data['plugins'][key][0]['installPath']); break
"
```

If the command prints nothing, the plugin is not registered — stop immediately and report: "Plugin root could not be resolved from installed_plugins.json." Do not guess or construct the path manually.

**CRITICAL**: The orchestrator must NOT run the compiled scripts or prepare commands itself. These are executed by the delegated `implementation-executor` subagent. The orchestrator only constructs the prompt template and launches the agent.

### Agent Prompt Template

When launching an `implementation-executor` agent for a task, you **must** use `subagent_type: "fortran-development-pipeline:implementation-executor"` in the Agent tool call — never use the default general-purpose agent. Using the wrong agent type means the coding standards and quality gates defined in the `implementation-executor` agent definition will not be applied.

Use this template as the prompt body:

```
You are executing {TASK_ID} from the plan document at {PLAN_PATH}.

Step 1: Write the verification sidecar for the post-task hook by running:
  bash {SCRIPT_PATH} prepare "{MANIFEST_PATH}" {TASK_ID}
This writes acceptance criteria so the SubagentStop hook can verify your work
automatically. Do this BEFORE making any code changes.

Step 2: Run the compiled script:
  bash {COMPILED_SCRIPT_PATH}

  IMPORTANT: A PostToolUse hook will automatically stop you after this
  command completes. You do NOT need to do anything else — no validation,
  no LSP checks, no summary. The SubagentStop hook handles verification,
  checkpointing, and committing. Just run the script and stop.

  If the Bash tool reports a non-zero exit code, that information is
  captured automatically. Do NOT attempt manual implementation — compiled
  scripts are the only execution path.
```

Replace:

- `{TASK_ID}` — the task identifier (e.g., `TASK-3`)
- `{PLAN_PATH}` — absolute path to the plan document
- `{SCRIPT_PATH}` — absolute path to the plugin's `scripts/task-sidecar.sh`
- `{MANIFEST_PATH}` — absolute path to `<plan-dir>/compiled/manifest.json`
- `{COMPILED_SCRIPT_PATH}` — absolute path to the compiled `.sh` script for this task, constructed as `<plan-dir>/compiled/<TASK-ID>.sh` (e.g., `plans/phase-4/compiled/TASK-3.sh`). The `compiled/` directory is a sibling of the plan file, generated by `/compile-plan`.

> **Critical**: The Agent tool call must always include `subagent_type: "fortran-development-pipeline:implementation-executor"` and `name: "{TASK_ID}"`. The `name` field makes the agent addressable via SendMessage and is used by the SubagentStop hook to locate the correct sidecar. Example skeleton:
>
> ```
> Agent({
>   subagent_type: "fortran-development-pipeline:implementation-executor",
>   name: "{TASK_ID}",
>   description: "Execute TASK-3: add INTENT declarations to density_mix subroutine",
>   prompt: "<filled template above>"
> })
> ```

## Checkpoint / Resume

Before doing anything else, check for a checkpoint file:

```
execution_reports/.checkpoint_<plan-slug>.json
```

where `<plan-slug>` is the plan filename without extension (e.g. `phase-4.1` for `phase-4.1.toml`).

**If the checkpoint exists**, read it:

```json
{
  "plan": "<plan-path>",
  "base_commit": "a1b2c3d4e5f6789...",
  "completed": ["TASK-1", "TASK-2"],
  "failed": [],
  "blocked": []
}
```

Note in your execution report: "Resuming from checkpoint — TASK-1, TASK-2 already completed. Starting from TASK-3." Then immediately proceed to **Execution Workflow** below, skipping tasks in `completed`/`failed`/`blocked`. Do NOT stop or wait for user input — every task must be delegated to an `implementation-executor` subagent via the Agent tool (`subagent_type: "fortran-development-pipeline:implementation-executor"`), exactly as described in the Execution Workflow.

**If no checkpoint exists**, start fresh. Proceed immediately to Execution Workflow step 1 — do not stop.

**During execution**: after each task passes validation, immediately update the checkpoint by adding the task ID to `completed` (or `failed`/`blocked` as appropriate). This ensures a crash or interruption at any point can be resumed from the last successful task.

**On full completion** (all tasks done, report written, commit created): delete the checkpoint file.

## Execution Workflow

1. **Parse the plan**:
   - Resolve `<plugin-root>` using the command in the "Task Sidecar Script" section above, then run `bash <plugin-root>/scripts/task-sidecar.sh list <plan-dir>/compiled/manifest.json` to enumerate all task IDs
   - Read the `[dependencies]` table in the TOML plan to determine the execution order. Tasks without dependencies can run first; dependent tasks run after their prerequisites complete. If no `[dependencies]` section exists, fall back to ascending ID order.
   - Extract global verification commands: gather all unique `acceptance` arrays from the tasks for final verification

2. **Compile the plan** (produces deterministic scripts for each task):
   - Look for `compiled/manifest.json` in the same directory as the plan file
   - If it does NOT exist, compile the plan by invoking `/compile-plan <plan-path>`, then read the resulting manifest
   - Read `compiled/manifest.json`. Every task has a pre-compiled shell script at `compiled/<TASK-ID>.sh` that applies changes deterministically via `sd -F`

3. **Execute tasks sequentially — one at a time**:
   - **CRITICAL — agent type**: For EVERY task, launch the `implementation-executor` **agent** via the Agent tool with `subagent_type: "fortran-development-pipeline:implementation-executor"`. This is mandatory — do NOT omit `subagent_type` and do NOT use the default general-purpose agent. The `implementation-executor` agent carries the coding standards and quality gates; a general-purpose agent will ignore them.
   - **CRITICAL**: Honour the plan's execution order. If the plan has a `[dependencies]` table or an "Execution Order" section, treat that order as mandatory. Do NOT start TASK-N+1 until TASK-N has passed validation. A prerequisite task that fails validation is a hard blocker — do not proceed past it to any task that depends on it.
   - **CRITICAL: Multi-part tasks are atomic**. If a task has sub-steps labelled (a), (b), (c) … or "Change 1 / Change 2 / …", delegate ALL sub-steps to a **single** `implementation-executor` agent call with the full list of sub-steps. Never split sub-steps across separate agent calls.
   - Complete each task fully before starting the next:
     1. Launch `implementation-executor` agent using the **Agent Prompt Template** above. Set `subagent_type: "fortran-development-pipeline:implementation-executor"`, pass the script path, plan path, task ID, and compiled script path (`<plan-dir>/compiled/<TASK-ID>.sh`). Do NOT copy task content into the prompt; the agent runs the compiled script directly.
     2. Wait for the agent to complete. **A SubagentStop hook automatically runs acceptance commands, updates the checkpoint, appends to the execution report, and commits.** The hook returns a `reason` field with ground-truth verification results — trust the hook's output over the subagent's claim of success/failure.
     3. Read the hook's verification results from the agent completion output. The hook reports PASSED/FAILED per acceptance command, checkpoint status, and commit hash.
     4. If the hook reports failure:
        - Launch a new `implementation-executor` agent (same `subagent_type`) to retry (up to 3 attempts total)
        - If still fails after 3 attempts: mark as failed, **stop execution entirely** for dependent tasks; mark dependents as "Blocked" in the report
     5. If the hook reports success: proceed to next task
   - Tasks declared **independent** (no entry in `[dependencies]`) MAY be launched in parallel — the sidecar mechanism uses per-task filenames (`current_task_{TASK_ID}.json`) so concurrent subagents do not conflict. Tasks with declared dependencies must remain sequential.
   - Never launch parallel agents for tasks that share a dependency — strict sequential order between dependent tasks preserves plan integrity

4. **Run global verification**:
   - After all tasks complete, run the union of all `acceptance` commands from the plan (deduplicated)
   - Capture stdout, stderr, and exit codes

5. **Finalize the execution report**:
   - The hook has already appended per-task results to `execution_reports/execution_<plan-slug>_<date>.md`
   - Append the **Summary** section (total tasks, passed, failed, overall status) and **Global Verification** results
   - Update the **Status** field in the report header

6. **Clean up**:
   - Delete the checkpoint file (`execution_reports/.checkpoint_<plan-slug>.json`) on full completion
   - The hook already committed per-task; create a final commit for the summary/global verification if needed

## Validation Logic

You should pass these to `implementation-executor` agent every time you assign the task.

For each task:

- **First**: Check LSP diagnostics immediately after code changes to catch syntax/type errors early
- **Then**: Run commands from the task's `acceptance` array
- Check exit codes (0 = success, non-zero = failure)
- For file creation tasks: verify files exist with LSP documentSymbol or file system checks
- For compilation tasks: Check LSP diagnostics first, then `gfortran -c -Wall <file>.f90` or project build command must succeed
- For test tasks: the project test suite must pass all tests
- For symbol renames: Use LSP rename instead of manual edits, verify with LSP references
- Capture full stdout and stderr for the report

**LSP validation workflow**:

1. After each edit: immediately check `LSP diagnostics` for the modified file
2. If diagnostics show errors: fix them before proceeding to acceptance commands
3. Use `LSP hover` to verify subroutine signatures match expectations
4. Use `LSP references` to ensure refactorings didn't break call sites

Global verification:

- Run the deduplicated union of all task `acceptance` commands
- All must succeed (exit code 0) for overall success

## Execution Report Format

```markdown
# Execution Report: <Phase Name>

**Plan**: <path-to-plan-file>
**Started**: <ISO 8601 timestamp>
**Completed**: <ISO 8601 timestamp>
**Status**: <All Passed | Partial Success | Failed>

## Task Results

### TASK-1: <description>

- **Status**: ✓ Passed
- **Attempts**: 1
- **Files modified**: <list>
- **Validation output**:
```

<stdout/stderr from acceptance checks>

```

### TASK-2: <description>
- **Status**: ✗ Failed
- **Attempts**: 3
- **Files modified**: <list>
- **Validation output**:
```

<stdout/stderr from last attempt>

```
- **Error**: <error message>

## Global Verification

```bash
<commands run>
```

**Output**:

```
<stdout/stderr>
```

**Result**: <Passed | Failed>

## Summary

- Total tasks: X
- Passed: Y
- Failed: Z
- Overall status: <All Passed | Partial Success | Failed>
```

## Commit Message Format

**All tasks pass**:

```
feat(phase-X.Y): <phase name from plan>

- TASK-1: <description>
- TASK-2: <description>
- TASK-3: <description>

All tasks completed successfully. See execution_reports/execution_phase-X.Y_<timestamp>.md
```

**Some tasks fail**:

```
feat(phase-X.Y): partial implementation of <phase name>

✓ TASK-1: <description>
✓ TASK-2: <description>
✗ TASK-3: <description> (failed after 3 attempts - <brief error>)

See execution_reports/execution_phase-X.Y_<timestamp>.md for details.
```

## Rules

- **Always use the task sidecar script for sidecar preparation** — run `scripts/task-sidecar.sh prepare` before each task to write the verification sidecar. The compiled script handles all code changes.
- **Use the task sidecar script for enumeration** — at the start, run `scripts/task-sidecar.sh list` to discover all task IDs from the manifest rather than parsing the document yourself.
- **Compiled scripts are the only execution path** — never attempt manual code changes based on task descriptions. If the compiled script fails, report the error and retry; do not fall back to LLM-interpreted edits.
- **Use LSP tools first** — before editing code, use LSP to understand structure; after editing, check LSP diagnostics before compiling
- **Do not ask questions** — if something is ambiguous, make the most conservative interpretation and proceed
- **Do not modify task instructions** — execute exactly what the plan specifies, nothing more
- **Do not add improvements** — no refactoring, no "while I'm here" changes, no extra features
- **Do not skip tasks** — even if a task seems redundant or already done, execute and validate it
- **Complete one task fully before moving to the next** — including all retry attempts
- **Respect declared execution order** — if the plan specifies an order, that order is mandatory. Validation must pass for each task before its dependents begin. A failed task is a hard blocker for all tasks that depend on it.
- **Multi-part tasks are a single atomic unit** — if a task contains labelled sub-steps (a/b/c… or Change 1/2/3…), all sub-steps go to one `implementation-executor` agent call. Never split them across separate agents. Partial application leaves the codebase in a broken intermediate state.
- **A failed prerequisite blocks dependents** — if TASK-N fails after 3 attempts and TASK-M depends on TASK-N, mark TASK-M as "Blocked (TASK-N failed)" in the report without attempting it.
- **If a task is already done** (e.g., INTENT already added, file already exists with correct content): note it briefly in the report, mark as passed, continue
- **Use conservative interpretation** — when in doubt, do the minimal thing that satisfies the task description
- **Ignore line numbers as addresses** — plan documents may reference `file.f90:42`; treat the number as a hint only. Before each edit, re-locate the target using LSP `documentSymbol`, `definition`, or `Grep` for unique surrounding context. This is mandatory for every edit in a multi-edit task.
- **Stage untracked files** — if a task creates new files, stage them for commit
- **Use modern tools** — `fd` instead of `find`, `rg` instead of `grep`
- **Prefer LSP for refactoring** — use LSP rename for symbol changes, not manual find-replace

## Error Handling

- **Plan file doesn't exist**: Error immediately with clear message, do not proceed
- **Plan structure malformed** (no `[tasks.*]` sections): Error with explanation, do not proceed
- **Task validation fails**: Log failure, retry up to 3 times, then mark as failed and continue
- **Global verification fails**: Note in report, include in commit message, but still commit
- **Git commit fails**: Report error to user, do not retry
