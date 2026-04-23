---
name: enrich-phase-plan
description: Orchestrates a multi-agent pipeline to produce a detailed, reviewed, executor-ready TOML implementation plan from a high-level plan document. Use this skill when the user asks to "enrich the plan", "elaborate the next implementation plan", "prepare the TOML plan", "break down the plan into tasks", or wants to turn a high-level plan file into a concrete, executor-ready task breakdown. Run after /plan-review has approved the plan.
---

# Enrich Phase Plan

Takes an approved high-level plan document and produces a detailed, executor-ready TOML implementation plan by running a pipeline of specialized agents over it.

## Pipeline

```
fortran-architect (elaborate)
       ↓
plan-decomposer (break down into TOML)
       ↓
impl-plan-reviewer (review)
       ↓ [loop if Needs More Detail]
plan-decomposer (revise)
       ↓
dry-run compile (validate)
       ↓ [loop if fails, max 2]
fortran-architect (final review)
```

## Step 1 — Identify the plan file

Ask the user for the path to the existing plan file if not already provided. Read it before proceeding.

## Step 1.5 — Load deferred improvements and failure patterns (optional)

**Deferred improvements**: Search for deferred improvement files from prior phases:

```bash
fd deferred.md notes/pr-reviews/
```

If any are found, read them and include their contents in the fortran-architect elaboration prompt (Step 2) as additional context. This gives the architect visibility into improvements that were explicitly deferred — some may now be relevant to include.

**Cross-round failure patterns**: Search for fix plans from prior phases:

```bash
fd fix-plan.toml notes/pr-reviews/ | head -3
```

If any are found, read them and extract recurring issue categories. Look for patterns such as:
- Missing `use ModuleName` statements (module wiring)
- Missing `public ::` declarations for symbols needed by other modules
- Incomplete consumer updates (module created but never `use`d)
- Stale `use` statements after refactoring
- Cross-module integration failures (subroutine placed in wrong module)

Summarize the recurring categories as `KNOWN_FAILURE_MODES`. These will be passed to the plan-decomposer in Step 3 as proactive checks.

If no fix plans or deferred files exist, proceed without this context.

## Step 2 — fortran-architect elaboration

Invoke the `fortran-development-pipeline:fortran-architect` agent with this prompt:

```
You are reviewing the following implementation plan for the NEXT phase of work on a Fortran scientific codebase.

<plan>
{{PLAN_FILE_CONTENTS}}
</plan>

<deferred_improvements>
{{DEFERRED_CONTENTS — or "None" if no deferred.md files were found}}
</deferred_improvements>

Your job:
1. Identify what the next phase is trying to achieve.
2. Elaborate on implementation details that are underspecified — concrete subroutine signatures, INTENT declarations, MODULE locations, KIND parameters, error handling strategy (IOSTAT/STAT patterns).
3. Call out architectural cautions: numerical stability pitfalls, interface mismatches, module boundary violations, API surface decisions that are hard to reverse, places where the plan may conflict with Fortran idioms or the project's established patterns.
4. **Use LSP tools** to understand the current codebase state:
   - Use `LSP documentSymbol` to locate existing modules/subroutines mentioned in the plan
   - Use `LSP hover` to verify current subroutine signatures before proposing changes
   - Use `LSP references` to understand how existing subroutines are called
   - Use `LSP definition` to navigate to type/interface definitions
   - Check `LSP diagnostics` to identify any existing issues that might affect the plan
5. Do NOT decompose into tasks. Output a single enriched narrative that a plan-decomposer agent can use as input.
```

Capture the output as `ELABORATED_PLAN`.

## Step 3 — plan-decomposer breakdown

Invoke the `fortran-development-pipeline:plan-decomposer` agent with this prompt:

```
Break down the following elaborated implementation plan into minimum-viable, SRP-aligned subtasks for a Fortran implementation-executor agent.

<elaborated_plan>
{{ELABORATED_PLAN}}
</elaborated_plan>

<known_failure_modes>
{{KNOWN_FAILURE_MODES — or "None" if no fix-plan.toml files were found}}
</known_failure_modes>

**Proactive failure prevention**: Review the known failure modes above. For each task in your decomposition, verify that it does not repeat any of these patterns. Pay special attention to module wiring (`use` statements) and consumer-side updates.

**Output format**: The plan MUST be a TOML file following the compilable-plan-spec so it can be compiled into deterministic `sd`-based scripts. Use this exact structure:

```toml
[meta]
title = "Phase X.Y: <Phase Name>"
source_branch = "<branch>"
created = "<YYYY-MM-DD>"

[dependencies]
# task_id = ["dep1", "dep2"]  — omit section if all tasks are independent

[tasks.TASK-N]
description = "Short description"
type = "replace"  # "replace" | "create" | "delete"
acceptance = [
    "gfortran -c -Wall src/module.f90",
    "make test",
]

[[tasks.TASK-N.changes]]
file = "relative/path/from/root.f90"
before = '''
exact content copied verbatim from the source file — no paraphrasing, no elision with `...`
'''
after = '''
exact replacement content
'''
```

**Rules for before/after blocks (critical for automated application):**
- **before** must be an exact substring of the target file — copy verbatim using `Read` tool or `git show`. Whitespace must match exactly.
- Include enough surrounding context so the before block matches uniquely in the file.
- For insertions: use a context block around the insertion point as before, and the same block with new code inserted as after (this is a `replace`).
- For tasks touching multiple locations in one or more files, use multiple `[[tasks.TASK-N.changes]]` entries, each with its own `file`, `before`, and `after`.
- Use multiline literal strings (`'''`) for code content. If code contains `'''`, use `"""` with escaping.
- Task IDs must match pattern: `TASK-N`.

**Additional guidance:**
- Include LSP tool usage hints: which LSP operations to use for understanding existing code (documentSymbol, hover, references)
- Do not reference code locations by line number — the before block is the address
- Include the dependency graph in the `[dependencies]` table
```

Capture the output as `DECOMPOSED_PLAN`.

## Step 4 — impl-plan-reviewer loop

Invoke the `fortran-development-pipeline:impl-plan-reviewer` agent with this prompt:

```
Review the following decomposed implementation plan. For each task, report whether it is CLEAR, UNCLEAR, or BLOCKED. End with your overall verdict.

<decomposed_plan>
{{DECOMPOSED_PLAN}}
</decomposed_plan>
```

- If the verdict is **Ready to Implement**: proceed to Step 4.5.
- If the verdict is **Needs More Detail**: collect the flagged issues, then re-invoke `fortran-development-pipeline:plan-decomposer` with the original decomposed plan plus the reviewer's feedback, asking it to revise only the flagged tasks. Repeat this loop (max 3 iterations). If still not passing after 3 iterations, surface the remaining issues to the user and ask how to proceed.

## Step 4.5 — Dry-run compilation

After the reviewer approves the decomposed plan, validate that the plan's combined changes produce a compilable project before the final architect review:

1. **Create a temporary worktree**:
   ```bash
   git worktree add /tmp/plan-dryrun-$(date +%s) HEAD
   ```

2. **Apply all changes** from the TOML plan to the worktree. For each task (in dependency order), for each `[[changes]]` entry:
   - If `type = "create"`: write the `after` content to the target file
   - If `type = "replace"`: use `sd -F` to replace `before` with `after` in the target file
   - If `type = "delete"`: use `sd -F` to remove `before` from the target file

3. **Run project-wide compilation** in the worktree:
   ```bash
   make 2>&1 || fpm build 2>&1
   ```

4. **Always clean up the worktree** (even on failure):
   ```bash
   git worktree remove --force /tmp/plan-dryrun-*
   ```

5. **If compilation succeeds**: proceed to Step 5.

6. **If compilation fails**: feed the error output back to the `fortran-development-pipeline:plan-decomposer` agent with this prompt:
   ```
   The decomposed plan failed dry-run compilation. Here are the errors:

   <compilation_errors>
   {{BUILD_OUTPUT}}
   </compilation_errors>

   Revise ONLY the tasks that caused these errors. Common causes:
   - Missing `use ModuleName` statement for a new module
   - Missing `public ::` declaration for a symbol needed by another module
   - Stale `use` path after a prior task moved or renamed a symbol
   - Interface mismatch from a changed subroutine signature

   Return the full revised TOML plan.
   ```
   Update `DECOMPOSED_PLAN` with the revision and repeat the dry-run (max 2 revision iterations). If still failing after 2 iterations, present the compilation errors to the user and ask how to proceed.

## Step 5 — fortran-architect final review

Invoke the `fortran-development-pipeline:fortran-architect` agent with this prompt:

```
You performed an architectural elaboration earlier. Now review the final decomposed plan below to ensure it has not drifted from the architectural intent.

<original_elaboration>
{{ELABORATED_PLAN}}
</original_elaboration>

<final_decomposed_plan>
{{DECOMPOSED_PLAN}}
</final_decomposed_plan>

Flag any tasks that:
- Contradict the architectural cautions you raised (numerical stability, interface mismatches, module boundary violations)
- Introduce scope creep beyond the next phase
- Are missing from the elaboration but implied by it
- Have acceptance criteria that would not actually verify correctness (e.g., compiler accepts it but algorithm is wrong)

End with: APPROVED or NEEDS REVISION (with specific items to fix).
```

If APPROVED: present the final plan to the user.
If NEEDS REVISION: apply the architect's corrections to the decomposed plan and present both the corrections and the final plan to the user.

## Output

Present the final plan as the primary output. Include a brief summary of:

- What the fortran-architect flagged during elaboration
- Any reviewer iterations that were needed
- Any final corrections from the architect review
