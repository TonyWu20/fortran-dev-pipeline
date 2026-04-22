---
name: review-pr
description: Review a branch's Fortran code against the project's plan, architecture, and style. Produces a PR rating and a detailed fix document for the author. Triggered by /review-pr [branch-name].
version: 0.1.0
---

# PR Review Skill

Reviews a branch against `main`, checks plan/spec/style compliance, rates the PR, and produces a fix document for the author.

## Trigger

`/review-pr [branch-name]`

If no branch is given, compare the current branch against `main`.

## Command-line Tools

- use `fd` instead of `find`
- use `rg` instead of `grep`

## Process

### Step 1: Load Context

Read these three things — nothing else:

1. **Project memory** (always):

   Derive the memory directory from the current working directory:

   ```bash
   MEMORY_DIR="$HOME/.claude/projects/$(pwd | sd '/' '-')/memory"
   ```

   - Read `$MEMORY_DIR/MEMORY.md` (the index). If it exists, read every memory file linked in the index.
   - If `MEMORY.md` does not exist but the directory does, read all `*.md` files in the directory directly.
   - If the directory is missing or empty, note that no project memory is available and proceed with the diff alone.

2. **Phase plan** (always):

   Locate the authoritative spec for this branch. Try in order:

   - Parse the branch name for a phase identifier (e.g., `phase-4-density-mixing` → look for `4`). Search: `fd -e md -e toml . plans/ | rg -i 'phase.?{N}'`
   - If a `plans/` directory exists and contains exactly one plan file, use it.
   - If the `notes/plan-reviews/` directory exists, check for a `decisions.md` file that names the plan path.
   - If no plan is found: note **"No phase plan found — scope gating disabled; all issues treated as [Correctness]."** and proceed without a spec.

   When found, read the full plan. This is the authoritative scope spec — issues must be justified against it.

3. **Branch status snapshot** (if it exists):
   ```
   notes/pr-reviews/{branch}/status.md
   ```
   The snapshot contains the post-fix diff, changed-file list, build/test results, and outstanding issues — it is the complete picture of the branch state.

**If the snapshot exists: stop reading here. Do not read any source files, run any git commands, or call any LSP tools. Proceed directly to Step 2 with the plan and snapshot in context.**

If no snapshot exists (first review), gather the diff before proceeding:

```bash
git fetch origin
git log --oneline main..{branch}
git diff main...{branch} --stat
git diff main...{branch}
```

Read each changed file in full using the Read tool only in this no-snapshot case.

### Step 2: Hand Off to fortran-architect

Pass everything you have loaded (memory + snapshot, or memory + diff) directly to the `fortran-development-pipeline:fortran-architect` agent. Do **not** perform any further file reads, LSP queries, or git commands yourself — the fortran-architect agent will evaluate the code from the context you supply.

In your handoff prompt include:

- The full content of the memory files
- The full content of the phase plan (or note that no plan was found — scope gating disabled)
- The full snapshot (or diff if no snapshot)
- The four evaluation axes (below) — ask the agent to score each axis and list issues
- Instruction: classify each issue as `[Defect]`, `[Correctness]`, or `[Improvement]` per the classification rule in Axis A

### Step 3: Evaluate Against Four Axes

The `fortran-development-pipeline:fortran-architect` agent evaluates four aspects:

**A. Plan & Spec Fulfillment**

- Does the code implement what the roadmap/phase requires?
- Are there missing pieces from the stated goal?
- Are there out-of-scope additions?

For every issue found across **all four axes**, assign one classification:
- `[Defect]` — code does not implement what the plan commissioned
- `[Correctness]` — incorrect behavior independent of the plan (bug, numerical error, missing INTENT, COMMON block introduced)
- `[Improvement]` — better design, but outside plan scope; the plan did not commission it

Only `[Defect]` and `[Correctness]` items enter the Fix Document. `[Improvement]` items are recorded in `deferred.md` — not as fix tasks.

**B. Architecture Compliance**

- Module encapsulation respected (no inappropriate USE of internals)?
- Subroutines have single responsibility?
- No implicit interfaces?
- INTENT declarations on all dummy arguments?
- No COMMON blocks introduced?
- Explicit interfaces via MODULE or INTERFACE blocks?

**C. Fortran Style & Quality**

- KIND parameters used consistently (`REAL(dp)` not bare `REAL`)?
- No magic numbers — named constants used?
- No GOTO?
- IMPLICIT NONE in every program unit?
- ALLOCATABLE used where appropriate?
- No dead code, unused variables, or commented-out blocks?

**D. Correctness & Numerical Quality**

- Numerical stability concerns (catastrophic cancellation, ill-conditioning)?
- Convergence criteria correct and consistent with the referenced algorithm?
- Algorithm matches the referenced paper/equation?
- Edge cases handled (zero denominators, empty arrays, un-allocated arrays)?
- Test coverage for new public subroutines?

### Step 4: Rate the PR

| Rating              | Criteria                                                                |
| ------------------- | ----------------------------------------------------------------------- |
| **Approve**         | All axes pass; minor nits only                                          |
| **Request Changes** | Fixable issues; correct direction but needs work                        |
| **Reject**          | Wrong approach, architectural violation, or incomplete spec fulfillment |

### Step 5: Verify the Fix Document

Launch the `fortran-development-pipeline:strict-code-reviewer` agent to fact-check every issue in the draft fix document. Pass it the full fix document draft and the snapshot (or diff). The agent will verify that each issue's cited file/subroutine exists and the described problem is present. Apply all corrections before writing the final output. Remove or mark `[unverified]` any issue the agent cannot confirm. Also verify that no `[Improvement]`-classified issue appears in the Fix Document — those belong in `deferred.md`, not in the fix plan.

### Step 5.5: Cross-Round Pattern Detection (conditional)

**Skip this step** if neither `notes/pr-reviews/{branch}/fix-plan.toml` nor `notes/pr-reviews/{branch}/fix-plan.md` exists in git history, or has fewer than 2 commits. This step only fires on re-reviews where prior fix plans were committed.

1. Retrieve the commit list for the fix plan file (oldest first). Check both `.toml` and legacy `.md`:

   ```bash
   git log --reverse --format='%H %s' -- notes/pr-reviews/{branch}/fix-plan.toml notes/pr-reviews/{branch}/fix-plan.md
   git log --reverse --format='%H %s' -- notes/pr-reviews/{branch}/review.md
   ```

2. For each commit hash, extract the historical version (try `.toml` first, fall back to `.md`):

   ```bash
   git show {hash}:notes/pr-reviews/{branch}/fix-plan.toml
   ```

   Parse `### TASK-N:` and `### Issue N:` headers plus their **Problem** / **Fix** fields. Label each version by its commit message suffix.

3. Compare across versions to detect two patterns:
   - **Recurring issues** — the same problem appears in non-consecutive versions (regression: fixed, then reintroduced)
   - **Contradictory fixes** — a task in one version directly reverses a task from a prior version

4. Summarise findings as a short list (max 5 items). Each item states:
   - Pattern type: `Recurring` or `Contradictory`
   - Which versions are involved (e.g., v1, v3)
   - The issue title or problem description

   If no patterns are found, note "None" and move on.

5. Pass findings to Step 6 for inclusion in the `**Cross-Round Patterns:**` field.

### Step 6: Output

Produce two sections using **exactly** the templates below. Do not rename headers, reorder fields, or omit any field — downstream tools parse these by pattern.

---

> **OUTPUT TEMPLATE — copy verbatim, fill in `{…}` placeholders**

```
## PR Review: `{branch}` → `main`

**Rating:** [Approve / Request Changes / Reject]

**Summary:** [2–3 sentences on overall quality and direction]

**Cross-Round Patterns:** [None / list items below]

- [Recurring] [issue title] — flagged in vX, vY (regression)
- [Contradictory] vX "[action]" vs vY "[opposite action]"

**Deferred Improvements:** [None / N items → `notes/pr-reviews/{branch}/deferred.md`]

**Axis Scores:**

- Plan & Spec: [Pass / Partial / Fail] — [one-line reason]
- Architecture: [Pass / Partial / Fail] — [one-line reason]
- Fortran Style: [Pass / Partial / Fail] — [one-line reason]
- Correctness & Numerical Quality: [Pass / Partial / Fail] — [one-line reason]
```

---

> **FIX DOCUMENT TEMPLATE — one block per issue, repeat as needed**

```
## Fix Document for Author

### Issue N: [Short title]

**Classification:** [Defect / Correctness]
**File:** `path/to/file.f90`
**Severity:** [Blocking / Major / Minor]
**Problem:** [What is wrong and why it matters]
**Fix:** [Concrete instruction — what to change, with a Fortran code snippet if helpful]
```

**Format rules (enforced by downstream scripts):**

| Field                 | Rule                                                                                                       |
| --------------------- | ---------------------------------------------------------------------------------------------------------- |
| `### Issue N:` header | Must use a colon (`:`) after the number — **not** an em dash (`—`). Scripts use `(?=:)` lookahead.         |
| `**Classification:**` | Exactly one of: `Defect`, `Correctness`. `[Improvement]` items do not appear here — they go to `deferred.md`. |
| `**File:**`           | Path only, no line number. Reference the target by subroutine/module name in **Problem** or **Fix** instead. |
| `**Severity:**`       | Exactly one of: `Blocking`, `Major`, `Minor`.                                                              |

---

> **DEFERRED IMPROVEMENTS TEMPLATE — write to `notes/pr-reviews/{branch}/deferred.md` when [Improvement] items exist**

```
## Deferred Improvements: `{branch}` — {YYYY-MM-DD}

### [Short title]
**Source:** Round {N} review
**Rationale:** [Why this is a better design — one paragraph explaining why it matters and what problem it solves]
**Candidate for:** Phase {N+1} plan
**Precondition:** [A concrete trigger that makes this worth doing, e.g. "second caller of this subroutine exists" — not "when we have time"]
```

**Deferred items rules:**
- Only `[Improvement]`-classified issues go here — never `[Defect]` or `[Correctness]`
- Each entry must have a concrete precondition — vague entries like "when we have time" are not useful
- If no `[Improvement]` items exist, do not create `deferred.md`

### Step 7: Decompose Fixes and Save

After the final output is written:

1. Launch the `fortran-development-pipeline:plan-decomposer` agent, passing it:
   - The full Fix Document from Step 6
   - The actual source of every cited file (read via `git show {branch}:<path>`)
   - Instruction: produce a fix plan in **TOML format** (compilable-plan-spec v2) so it can be compiled into deterministic `sd`-based scripts. The output file is `fix-plan.toml`. Each task uses this structure:

   ```toml
   [tasks.TASK-N]
   description = "Short description"
   type = "replace"  # or "create" or "delete"
   acceptance = ["gfortran -c -Wall src/module.f90", "make test"]

   [[tasks.TASK-N.changes]]
   file = "relative/path/from/root.f90"
   before = '''
   exact content copied verbatim from the source file
   '''
   after = '''
   exact replacement content
   '''
   ```

   For tasks with multiple changes (same or different files), repeat `[[tasks.TASK-N.changes]]`:

   ```toml
   [[tasks.TASK-N.changes]]
   file = "path/to/first_file.f90"
   before = '''
   ...
   '''
   after = '''
   ...
   '''

   [[tasks.TASK-N.changes]]
   file = "path/to/second_file.f90"
   before = '''
   ...
   '''
   after = '''
   ...
   '''
   ```

   Dependencies go in a `[dependencies]` table (omit if all tasks are independent):

   ```toml
   [dependencies]
   TASK-3 = ["TASK-1", "TASK-2"]
   ```

   **Rules for before/after content (critical for automated application):**
   - `before` must be an exact substring of the target file — copy verbatim using `git show {branch}:<path>`. No paraphrasing, no elision with `...`. Whitespace must match exactly.
   - Include enough surrounding context so the `before` block matches uniquely in the file.
   - For insertions: use a context block around the insertion point as `before`, same block with new code inserted as `after`.
   - Each `[[tasks.X.changes]]` entry MUST have its own `file` field — never combine multiple paths.
   - Task IDs must match pattern: `TASK-N`, `Issue-N`, or `Fix-N`.
   - Acceptance commands must be valid shell commands that exit 0 on success.
   - Identify dependency relationships in the `[dependencies]` table.

2. Pass the `fortran-development-pipeline:plan-decomposer` output to the `fortran-development-pipeline:strict-code-reviewer` agent to verify:
   - Each "Before" snippet matches the actual file on the branch
   - Each "After" snippet is a valid minimal fix
   - Each verification command is correct for this project's build system
   - Flag steps where before-code doesn't match reality as NEEDS CORRECTION

3. Launch the `fortran-development-pipeline:fix-plan-reader` agent, passing it the saved `fix-plan.toml`. It will report whether each step is clear enough for a junior agent to follow without confusion. If it returns any ⚠️ UNCLEAR or ❌ BLOCKED items, revise those steps before finishing.

4. Save outputs to `notes/pr-reviews/{branch}/fix-plan.toml` with version control:

   Always **overwrite** `fix-plan.toml` completely with the new content — never prepend or append. Git history is the version record.

   ```bash
   git add notes/pr-reviews/{branch}/fix-plan.toml
   git commit -m "review({branch}): update fix plan"
   ```

   If the file does not exist yet, create it and use the same commit command.

5. If any `[Improvement]` items were identified during the review, write them to `notes/pr-reviews/{branch}/deferred.md` using the **Deferred Improvements template** from Step 6.

   - If `deferred.md` already exists (from a prior review round), **append** new items under a new dated heading — do not overwrite. Git history is the complete record, but the file accumulates items across rounds so the next `/plan-review` run has the full list.
   - If no `[Improvement]` items were found, do not create or modify `deferred.md`.

   ```bash
   git add notes/pr-reviews/{branch}/deferred.md
   git commit -m "review({branch}): record deferred improvements"
   ```

---

## Boundaries

**Will:**

- Use snapshot when available — read nothing extra
- Cite specific file and subroutine/module name for every issue
- Distinguish blocking issues from style nits
- Reference project architecture principles by name

**Will not:**

- Read source files when a snapshot is present
- Rewrite code for the author (describe the fix, don't do it)
- Flag issues outside the diff scope
- Penalize valid deviations if the author explains the reason in a comment
