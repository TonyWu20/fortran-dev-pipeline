---
name: compile-plan
description: Compile fix/implementation plan documents into deterministic sd-based scripts that apply code changes without LLM interpretation. Use when the user says "/compile-plan <path>", "compile the plan", "generate scripts from the plan", or wants to turn a Before/After plan into executable scripts. Also use proactively after a plan-decomposer or review-pr produces a fix-plan.toml with Before/After blocks.
---

# compile-plan

Compiles TOML plan documents with Before/After content into per-task shell
scripts that apply changes via `sd -F` (fixed-string replacement). The LLM
executor just runs the script instead of interpreting code changes.

## Invocation

`/compile-plan <path-to-plan.toml>`

## Plugin Root Resolution

**Resolve `<plugin-root>` before running any command below.** Run the following command once and record the printed path — use it as the literal value everywhere `<plugin-root>` appears:

```bash
python3 -c "
import json; from pathlib import Path
p = Path.home() / '.claude/plugins/installed_plugins.json'
data = json.loads(p.read_text())
for key in ['fortran-dev-pipeline@my-claude-marketplace', 'fortran-dev-pipeline@local']:
    if key in data['plugins']:
        print(data['plugins'][key][0]['installPath']); break
"
```

If the command prints nothing, the plugin is not registered — stop immediately and report: "Plugin root could not be resolved from installed_plugins.json." Do not guess or construct the path manually.

## How It Works

1. Parses the TOML plan document — each task has explicit `file`, `before`, `after` fields
2. Generates a Python runner per task with base64-encoded content + `sd -F` calls
3. Produces a `manifest.json` listing all compiled tasks

The generated scripts handle:
- **replace**: `sd -F <before> <after> <file>` (including insertions via context anchors)
- **create**: write file content directly
- **delete**: `sd -F <before> '' <file>`
- **multi-change**: sequential `sd` calls within a single task script

## Usage

```bash
# Compile a TOML plan (generates compiled/ directory sibling to the plan)
python3 <plugin-root>/skills/compile-plan/scripts/compile_plan.py <plan.toml>

# Dry run — parse and report without generating scripts
python3 <plugin-root>/skills/compile-plan/scripts/compile_plan.py <plan.toml> --dry-run

# Custom output directory
python3 <plugin-root>/skills/compile-plan/scripts/compile_plan.py <plan.toml> --output-dir /tmp/compiled

# Legacy markdown plans still work (with deprecation warning)
python3 <plugin-root>/skills/compile-plan/scripts/compile_plan.py <plan.md>
```

## Plan Format (TOML)

Plans must conform to the Compilable Plan Spec v2. Read the full spec at:
`<plugin-root>/skills/compile-plan/references/compilable-plan-spec.md`

Quick summary — a plan looks like:

```toml
[meta]
title = "Fix Plan Title"

[tasks.TASK-1]
description = "Short description"
type = "replace"
acceptance = ["gfortran -c -Wall src/module.f90"]

[[tasks.TASK-1.changes]]
file = "relative/path/from/root.f90"
before = '''
exact content copied from source file
'''
after = '''
replacement content
'''
```

Key rules:
- Each `[[tasks.X.changes]]` entry has its own `file` field — no ambiguity
- `before` must be an exact substring of the target file (copied verbatim)
- For insertions: `before` is a context anchor, `after` is the same context with new code
- Task IDs match pattern: `TASK-N`, `Issue-N`, `Fix-N`

## Output Structure

```
compiled/
├── manifest.json      # Task index with types, files, acceptance commands
├── TASK-1.sh          # Bash wrapper (just calls the .py)
├── TASK-1.py          # Python runner with base64 content + sd calls
├── TASK-2.sh
├── TASK-2.py
└── ...
```

## Integration with Executor Skills

The `fix` and `implementation-executor` skills should check for compiled scripts
before launching LLM agents. If `compiled/manifest.json` exists sibling to the
plan file and lists the current task with type != `manual`:

**Compiled task prompt** (replaces the standard agent prompt):
```
You are executing {TASK_ID} from {DOC_PATH}.

Step 1: Write the verification sidecar:
  bash {SCRIPT_PATH} prepare "{DOC_PATH}" {TASK_ID}

Step 2: Run the compiled script:
  bash {COMPILED_DIR}/{TASK_ID}.sh

Step 3: If exit code is 0, run acceptance: {ACCEPTANCE_SUMMARY}
        If exit code is non-zero, report the error output verbatim.
```

## Error Handling

- `sd` returns exit 0 even when no match is found — the generated scripts
  verify the Before pattern exists via Python `in` check before calling `sd`
- If pattern not found: script exits 1, task is marked failed in checkpoint
- No LLM fallback — failed scripts mean the plan's `before` block doesn't match
  the current source. The planner must regenerate.

## Legacy Markdown Support

Markdown `.md` plans are still supported but deprecated. The compiler prints a
warning and uses regex-based parsing. Multi-file `**File:**` fields (e.g.,
`` `a.toml` and `b.f90` ``) are now rejected with a clear error — use per-change
`**File:**` fields instead. Migrate to TOML for reliability.
