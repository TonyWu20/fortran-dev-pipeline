---
title: "Scope gate in review-pr + deferred.md channel for out-of-scope improvements"
type: feature-request
status: implemented
date: 2026-04-23
---

## Problem

The `review-pr` skill applies abstract architectural judgment without reference to
what the plan actually commissioned. This causes two failure modes:

1. **Reviewer-driven scope creep** — issues that are architecturally correct but
   out of scope get added to the fix plan, each fix round proposes new abstractions,
   and the implementation drifts away from the original plan through accumulated
   reviewer suggestions rather than deliberate design decisions.

   Example: Phase 4 plan said "add Pulay mixing to the density mixer". Fix rounds
   introduced a new `mixing_history_t` derived type, then flagged that its `get()`
   accessor leaks the backing array, then flagged the mixing algorithm belongs in a
   separate module. Five rounds of fixes for a plan that wanted one subroutine in
   one place.

2. **Suppressed legitimate improvements** — when the plan has a genuine architectural
   gap, the reviewer flags it but it lands in a fix plan rather than the plan itself.
   The gap reappears in the next phase because it was patched, not planned.

## Proposed Solution: Two-Gate System

### Gate 1 — Plan Review (before implementation)

A dedicated pass before implementation asks: *does the plan itself produce a good
design?* Architectural gaps get caught here and either:
- absorbed into the plan (plan is updated), or
- explicitly deferred with a stated reason

Once implementation starts, the plan is the authority.

### Gate 2 — Scoped Implementation Review (after implementation)

The `review-pr` skill prompt gets an explicit scope rule:

> An issue is in-scope only if it is a **defect** relative to the plan. A design
> improvement the plan didn't commission is out of scope — record it as a Phase N+1
> candidate in `notes/pr-reviews/{branch}/deferred.md`, not as a fix-plan item.

Each issue in the fix document must carry one of three classifications:

| Class | Meaning | Goes to |
|---|---|---|
| `[Defect]` | Code doesn't do what the plan says | Fix plan |
| `[Correctness]` | Wrong behavior regardless of plan (bug, missing INTENT, numerical error) | Fix plan |
| `[Improvement]` | Better design, but plan didn't commission it | `deferred.md` |

Only `[Defect]` and `[Correctness]` items enter the fix plan. `[Improvement]` items
go to `deferred.md` in the same commit.

### Absorbing Good Improvements Without Bloat

When the reviewer identifies something genuinely better:

1. Record in `notes/pr-reviews/{branch}/deferred.md` with rationale:
   > "Pulay mixing history should be encapsulated in a dedicated MODULE when a second
   > mixing scheme (Kerker, Anderson) is added. Candidate for Phase 5 plan."

2. At Phase N+1 plan review, the plan author reads `deferred.md` and decides:
   incorporate, defer again, or close as "won't do." This is a deliberate decision,
   not a side effect of a fix round.

3. The plan is updated before implementation starts — not discovered during fix rounds.

## Concrete Changes Required

### 1. Update `skills/review-pr/` skill prompt

In the **Fix Document** section, add:

```
Before writing each issue, classify it as one of:
- [Defect] — code does not implement what the plan commissioned
- [Correctness] — incorrect behavior independent of plan (bug, missing INTENT, numerical instability)
- [Improvement] — better design, but outside plan scope

Only [Defect] and [Correctness] issues enter the Fix Document / fix plan.
[Improvement] issues are written to `notes/pr-reviews/{branch}/deferred.md`
with a one-paragraph rationale explaining why the improvement is worth revisiting
in a future plan.
```

In **Step 7 (Decompose Fixes and Save)**, add:

```
After writing fix-plan.toml, also write `notes/pr-reviews/{branch}/deferred.md`
if any [Improvement] items were identified. Format:

## Deferred Improvements: `{branch}` — {date}

### [title]
**Source:** Round N review
**Rationale:** [why it's a better design]
**Candidate for:** Phase N+1 plan
**Precondition:** [what would make this worth doing, e.g. "second mixing scheme added"]
```

### 2. Add `skills/plan-review/` skill (new)

A short skill invoked before implementation starts. It reads:
- The phase plan (`plans/{phase}/PHASE_PLAN.md`)
- `notes/pr-reviews/{prev-branch}/deferred.md` (improvements deferred from last round)

And asks the fortran-architect agent:
- Does the plan produce a sound design?
- Are any deferred items from the previous phase now in-scope?
- Are there architectural gaps the plan doesn't address (missing INTENT patterns, interface issues, numerical stability)?

Output: an amended plan or a decision log for each deferred item.

### 3. Review prompt should load the plan file

Currently `review-pr` loads the status snapshot and memory but not the phase plan.
The fortran-architect agent therefore has no authoritative spec to check issues against.

Add to **Step 1 (Load Context)**:

```
3. Phase plan (always):
   Read `plans/{phase}/PHASE_PLAN.md` (or the nearest parent plan file).
   This is the authoritative spec. Issues must be justified against it.
```

## Acceptance Criteria

- A `review-pr` run on a compliant branch produces a `deferred.md` alongside
  `fix-plan.toml` when out-of-scope improvements are found.
- Fix plans contain only `[Defect]` and `[Correctness]` items.
- After 3 phases, no phase plan has been modified by a fix round rather than a plan
  review round.
- `deferred.md` entries from phase N appear in phase N+1 plan review and receive an
  explicit decision.
