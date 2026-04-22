# Compilable Plan Spec v2 (TOML Format)

This document defines the canonical TOML format for plan/fix documents that can
be compiled into deterministic `sd`-based scripts by `compile-plan`.

## Why TOML

- Multiline literal strings (`'''`) preserve code exactly — no indentation issues, no escaping
- Explicit `file` field per change eliminates regex-based file path extraction
- Array-of-tables (`[[tasks.X.changes]]`) maps naturally to ordered change lists
- Standard parser — no regex needed for metadata

## Schema

```toml
[meta]                              # optional
title = "Plan Title"
source_branch = "branch-name"
created = "YYYY-MM-DD"

[dependencies]                      # optional — omit means all parallel
# task_id = ["dep1", "dep2"]
# TASK-3 = ["TASK-1", "TASK-2"]    # TASK-3 depends on 1 and 2

[tasks.<TASK-ID>]                   # required, one per task
description = "Short description"
type = "replace"                    # "replace" | "create" | "delete"
acceptance = [                      # list of shell commands
    "gfortran -c -Wall src/module.f90",
    "make test",
]

[[tasks.<TASK-ID>.changes]]         # one or more change entries
file = "relative/path/from/root"    # required
before = '''
exact content to match
'''
after = '''
replacement content
'''
```

## Field Reference

### `[meta]` (optional)

| Field | Type | Description |
|-------|------|-------------|
| `title` | string | Human-readable plan title |
| `source_branch` | string | Branch this plan targets |
| `created` | string | ISO date |

### `[dependencies]` (optional)

Maps task IDs to arrays of prerequisite task IDs. Omitting means all tasks
are independent and can run in parallel.

```toml
[dependencies]
TASK-3 = ["TASK-1", "TASK-2"]
```

### `[tasks.<TASK-ID>]`

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `description` | string | yes | Short description of the change |
| `type` | string | no | `"replace"`, `"create"`, or `"delete"`. Inferred from changes if omitted. |
| `acceptance` | string[] | yes | Shell commands to verify the change |

### `[[tasks.<TASK-ID>.changes]]`

Each change entry is one Before/After replacement targeting a specific file.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `file` | string | yes | Relative path from project root |
| `before` | string | for replace/delete | Exact content to match in the file |
| `after` | string | for replace/create | Replacement content |

## Type Semantics

| Type | Before | After | Behavior |
|------|--------|-------|----------|
| `replace` | required | required | Find `before` in file, replace with `after` |
| `create` | omitted | required | Write `after` to new file at `file` path |
| `delete` | required | omitted | Find `before` in file, remove it |

If `type` is omitted, it is inferred:
- Both `before` and `after` present → `replace`
- Only `after` → `create`
- Only `before` → `delete`

## TOML String Handling

Use **multiline literal strings** (`'''`) for code content:

```toml
before = '''
SUBROUTINE old_name(x, y)
    REAL(dp), INTENT(IN) :: x
    REAL(dp), INTENT(OUT) :: y
'''
```

The compiler strips exactly one leading newline (the one immediately after `'''`)
and one trailing newline (the one immediately before closing `'''`). This matches
TOML's multiline literal string semantics.

**Important:** If your code content contains `'''`, use basic multiline strings
(`"""`) with escaped sequences, or split the change into smaller pieces that
avoid the triple-quote.

## Rules for Plan Authors

1. **Copy `before` blocks verbatim from source.** Use `Read` tool or `git show`
   to get exact content. Never paraphrase or abbreviate with `...`.
2. **Include enough context for unique matching.** The `before` block must appear
   exactly once in the target file.
3. **Match whitespace exactly.** Spaces vs tabs, trailing whitespace, blank
   lines — all must match.
4. **Use relative paths from project root.** Not absolute paths.
5. **One file per change entry.** Each `[[tasks.X.changes]]` targets exactly
   one file. Multi-file tasks use multiple change entries.
6. **No line numbers as addresses.** The `before` content is the address.
7. **Task IDs must match pattern:** `TASK-N`, `Issue-N`, `Fix-N`, or `FIX-N`.
8. **Acceptance commands must be valid shell commands** that exit 0 on success.

## Complete Example

```toml
[meta]
title = "Phase 4 Fix Plan"
source_branch = "phase-4"
created = "2026-04-20"

[dependencies]
# All independent — section empty

[tasks.TASK-1]
description = "Add INTENT declarations to compute_exchange_energy dummy arguments"
type = "replace"
acceptance = [
    "gfortran -c -Wall src/exchange.f90",
    "make test",
]

[[tasks.TASK-1.changes]]
file = "src/exchange.f90"
before = '''
SUBROUTINE compute_exchange_energy(rho, n_pts, energy)
    REAL(dp) :: rho(n_pts)
    INTEGER :: n_pts
    REAL(dp) :: energy
'''
after = '''
SUBROUTINE compute_exchange_energy(rho, n_pts, energy)
    REAL(dp), INTENT(IN) :: rho(n_pts)
    INTEGER, INTENT(IN) :: n_pts
    REAL(dp), INTENT(OUT) :: energy
'''

[tasks.TASK-2]
description = "Replace bare REAL with REAL(dp) KIND parameter in density_mix module"
type = "replace"
acceptance = ["gfortran -c -Wall src/density_mix.f90"]

[[tasks.TASK-2.changes]]
file = "src/density_mix.f90"
before = '''
    REAL :: alpha, beta, residual
'''
after = '''
    REAL(dp) :: alpha, beta, residual
'''

[[tasks.TASK-2.changes]]
file = "src/density_mix.f90"
before = '''
    REAL :: mix_coeff(MAX_ITER)
'''
after = '''
    REAL(dp) :: mix_coeff(MAX_ITER)
'''
```
