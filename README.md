# fortran-dev-pipeline

A Claude Code plugin that orchestrates a full plan-implement-review-fix development loop for Fortran scientific projects.

## Overview

`fortran-dev-pipeline` adds seven slash commands, six specialized agents, and two automation hooks to Claude Code. Together they enforce a structured workflow where:

- **Plans are reviewed architecturally** before any code is written (Gate 1)
- **Plans are compiled into deterministic shell scripts** — `sd -F` fixed-string replacements with base64-encoded content — so code changes are applied exactly as written, with no LLM reinterpretation at execution time
- **Every task is auto-verified, checkpointed, and committed** by hooks after execution, independent of agent behavior
- **Code review is scope-bound** to the phase plan (Gate 2), so architectural improvements are deferred rather than silently introduced via fix rounds

The pipeline targets DFT/EDFT-style numerical Fortran codes (Fortran 90/95/2003/2008), but the workflow is general enough for any Fortran project.

## Pipeline

```
/next-phase-plan
      │  Conversational planning with fortran-architect
      │  Output: plans/phase-{N}/PHASE_PLAN.md
      ▼
/plan-review                         ← Gate 1
      │  Architectural gate + deferred-items triage
      │  Output: notes/plan-reviews/{slug}/decisions.md
      ▼
/enrich-phase-plan
      │  architect elaboration → plan-decomposer TOML → impl-plan-reviewer loop → architect final review
      │  Output: plans/phase-{N}/impl-plan.toml
      ▼
/compile-plan
      │  Compiles TOML tasks into sd-F scripts + manifest.json
      │  Output: plans/phase-{N}/compiled/
      ▼
/implementation-executor
      │  Launches implementation-executor subagents per task
      │  Hooks: auto-verify, checkpoint, commit after each task
      ▼
/review-pr                           ← Gate 2
      │  Scoped review: [Defect] and [Correctness] → fix plan
      │  [Improvement] → notes/pr-reviews/{branch}/deferred.md
      │  Output: notes/pr-reviews/{branch}/fix-plan.toml
      ▼
/compile-plan  →  /fix
      │  Same execution path as implementation
      │  Output: notes/pr-reviews/{branch}/status.md
      │
      └──► back to /review-pr until branch is approved
```

Deferred improvements from Gate 2 feed back into `/next-phase-plan` for the next cycle.

## Skills

| Skill | Description |
|---|---|
| `/next-phase-plan` | Conversational phase planning with `fortran-architect`; produces `PHASE_PLAN.md` |
| `/plan-review` | Pre-implementation architectural gate; triages deferred items from prior phases |
| `/enrich-phase-plan` | Multi-agent pipeline that elaborates a plan into a TOML task breakdown |
| `/compile-plan` | Compiles TOML plans into deterministic `sd -F` scripts and a `manifest.json` |
| `/implementation-executor` | Orchestrates compiled-script execution via subagents with hook-based verification |
| `/review-pr` | Scope-bound code review; classifies issues and produces a `fix-plan.toml` |
| `/fix` | Runs a fix plan through the same compiled-script execution path |

## Agents

| Agent | Model | Role |
|---|---|---|
| `fortran-architect` | Opus | Senior architect; first-principles design, plan elaboration, code review |
| `plan-decomposer` | Opus | Breaks plans into SRP-aligned, dependency-ordered TOML subtasks |
| `impl-plan-reviewer` | Haiku | Simulates a junior developer; flags UNCLEAR or BLOCKED tasks before execution |
| `implementation-executor` | Haiku | Code-writing workhorse; executes a single delegated subtask |
| `strict-code-reviewer` | Opus | Fact-checks fix documents against actual files to prevent hallucinated changes |
| `fix-plan-reader` | Haiku | Simulates a junior developer reading a fix plan; verifies before/after clarity |

## Hooks

Two hooks automate the verification and bookkeeping that would otherwise require agent judgment:

**`PostToolUse` — `hooks/post_compiled_script.py`**
Fires after every `Bash` tool call. When it detects that an `implementation-executor` subagent just ran a compiled script (`compiled/TASK-\d+\.sh`), it blocks the agent immediately. This hands control to the SubagentStop hook before the agent can do anything else.

**`SubagentStop` — `hooks/verify_impl_task.py`**
Fires when an `implementation-executor` subagent exits. It reads the task's sidecar file (written by `scripts/task-sidecar.sh prepare` before the script runs), executes all acceptance commands, updates the checkpoint file, appends to the execution report, deletes the sidecar, stages all changes, and creates a git commit — all without LLM involvement.

The sidecar file (`~/.claude/hooks/current_task_{TASK_ID}.json`) is the communication channel between the orchestrating skill and the hooks. It carries the task ID, description, plan slug, and acceptance commands.

## Prerequisites

- [Claude Code](https://claude.ai/code) CLI
- [`sd`](https://github.com/chmln/sd) — used by compiled scripts for fixed-string replacement
- Python 3 — used by the compile script and hooks
- `gfortran` — used by acceptance commands in implementation tasks
- `git` — hooks commit after each verified task

## Installation

Clone this repository into your Claude Code plugins directory:

```sh
git clone https://github.com/TonyWu20/fortran-dev-pipeline ~/.claude/plugins/fortran-dev-pipeline
```

Then register it in your Claude Code settings:

```json
{
  "plugins": ["~/.claude/plugins/fortran-dev-pipeline"]
}
```

Restart Claude Code. The seven skills will be available as slash commands in any session.

## Project layout

```
.claude-plugin/plugin.json       Plugin manifest
agents/                          Agent definition files
hooks/
  hooks.json                     Hook registrations
  post_compiled_script.py        PostToolUse hook
  verify_impl_task.py            SubagentStop hook
scripts/
  task-sidecar.sh                Sidecar writer (list / prepare subcommands)
skills/
  compile-plan/
    SKILL.md
    scripts/compile_plan.py
    references/compilable-plan-spec.md
  enrich-phase-plan/SKILL.md
  fix/SKILL.md
  implementation-executor/SKILL.md
  next-phase-plan/SKILL.md
  plan-review/SKILL.md
  review-pr/SKILL.md
```

## License

MIT © 2026 Tony
