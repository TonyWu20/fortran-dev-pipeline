---
name: plan-review
description: Review a phase plan for architectural soundness before implementation begins. Reads the plan and any deferred improvements from prior phases, then asks the fortran-architect agent to validate the design, decide on deferred items, and surface architectural gaps. Use when the user says "/plan-review", "review the plan before implementing", or "check the plan first".
---

# Plan Review Skill

Reviews a phase plan for soundness before implementation starts. Validates the design, incorporates deferred improvements from prior phases, and produces a decision log. This is **Gate 1** — run it after `/next-phase-plan` and before `/enrich-phase-plan`.

## Trigger

`/plan-review [plan-path]`

If no path is given, ask the user.

## Command-line Tools

- use `fd` instead of `find`
- use `rg` instead of `grep`

## Process

### Step 1: Identify Inputs

1. **Phase plan**: If `[plan-path]` was given, read it. Otherwise ask the user:

   > "Which plan file should I review? (e.g., `plans/phase-4/PHASE_PLAN.md`)"

   Read the full plan content.

2. **Deferred improvements** (from prior phases):

   ```bash
   fd deferred.md notes/pr-reviews/
   ```

   - If one or more `deferred.md` files are found, list them to the user and read all of them. Include their contents in the review.
   - If none exist, proceed without deferred context.

3. **Project memory** (always):

   ```bash
   MEMORY_DIR="$HOME/.claude/projects/$(pwd | sd '/' '-')/memory"
   ```

   Read `$MEMORY_DIR/MEMORY.md` and every linked memory file.

### Step 2: fortran-architect Review

Pass everything to the `fortran-development-pipeline:fortran-architect` agent with this prompt:

```
You are performing a pre-implementation architectural review of a Fortran scientific project.

<phase_plan>
{{PLAN_CONTENTS}}
</phase_plan>

<deferred_improvements>
{{DEFERRED_CONTENTS — or "None" if no deferred.md files were found}}
</deferred_improvements>

<project_memory>
{{MEMORY_CONTENTS}}
</project_memory>

Answer these three questions:

1. **Design soundness**: Does this plan produce a sound design?
   - Are there architectural gaps the plan doesn't address?
   - Are there numerical stability pitfalls, interface mismatches, or API decisions that are hard to reverse?
   - Are there missing error handling strategies or module boundary violations?
   - Are there INTENT declaration patterns, KIND parameter conventions, or interface requirements implied but not stated?
   - If the plan looks sound, say so explicitly.

2. **Deferred item decisions**: For each item in `<deferred_improvements>`, decide:
   - **Absorb** — incorporate it into this plan (specify which section to update and how)
   - **Defer again** — not yet warranted (specify a concrete updated precondition)
   - **Close** — not worth doing (explain why)

3. **Recommended plan amendments**: List concrete additions or changes to the plan, if any. If no changes are needed, state "No amendments required."
```

Capture the output as `ARCHITECT_REVIEW`.

### Step 3: Output and Save

Produce two artifacts:

**1. Plan amendments** (if recommended):

Present the architect's suggested changes to the user. If they approve, update the plan file accordingly.

**2. Decision log**:

Write `notes/plan-reviews/{plan-slug}/decisions.md` where `{plan-slug}` is the plan filename without extension:

```markdown
## Plan Review Decisions — {plan-slug} — {YYYY-MM-DD}

### Design Assessment

{architect's soundness verdict — one paragraph}

### Deferred Item Decisions

#### {item title from deferred.md}
**Decision:** [Absorb / Defer again / Close]
**Rationale:** {architect's reasoning}
**Action:** {If Absorb: which section of the plan to update. If Defer: updated precondition. If Close: why not needed.}

### Plan Amendments

{List of amendments applied, or "None"}
```

Commit the decision log:

```bash
git add notes/plan-reviews/{plan-slug}/decisions.md
git commit -m "plan-review({plan-slug}): architectural review and deferred item decisions"
```

Report to the user: review complete, N deferred items decided, M plan amendments applied (or none).

## Boundaries

**Will:**
- Evaluate the plan's design before any code is written
- Give a concrete decision (Absorb / Defer / Close) for every deferred item
- Apply plan amendments only with user approval

**Will not:**
- Decompose the plan into TOML tasks (that is `/enrich-phase-plan`'s job)
- Modify the fix plan or source code
- Re-open items that were previously Closed without new evidence
