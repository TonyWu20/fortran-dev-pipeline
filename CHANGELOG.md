# Changelog

## [1.1.0] - 2026-04-24

### Added

- **`enrich-phase-plan`: dry-run compilation gate (Step 4.5)** — after the impl-plan-reviewer approves the TOML plan, all changes are applied to a temporary git worktree and the project is built. If compilation fails, errors are fed back to `plan-decomposer` for up to 2 revision iterations before surfacing to the user.
- **`enrich-phase-plan`: cross-round failure pattern ingestion** — the skill now searches prior `fix-plan.toml` files, extracts recurring issue categories (missing `use` statements, missing `public ::` declarations, stale imports, etc.), and passes them to `plan-decomposer` as `KNOWN_FAILURE_MODES` for proactive prevention.
- **`verify_impl_task.py`: workspace-level compilation gate** — after per-task acceptance commands pass, the hook now runs `$FORTRAN_BUILD_CMD` (default: `make 2>&1`) against the full project. Cross-module breakages (stale imports, missing `use` statements) that per-file checks miss are caught here. Configurable via the `FORTRAN_BUILD_CMD` environment variable.
- **`verify_impl_task.py`: configurable timeout** — `run_command` now accepts a `timeout` parameter (default 120 s); the workspace build uses 180 s.
- **`implementation-executor` and `fix`: diagnostic retry protocol** — before launching a retry agent on hook failure, the orchestrator now classifies the failure (content shifted / already applied / content missing) by grepping the target file for distinctive substrings from the `before` block, then includes the classification in the retry prompt.
- **`implementation-executor` and `fix`: post-round lint sweep** — after all tasks complete, a full project build with `-Wall -Wextra` flags is run. Warnings/errors in files touched this round are blocking; warnings in untouched files are noted but non-blocking.
- **`plan-decomposer`: completeness envelope rule** — every task must leave the project in a compilable, reachable state. Creating a module and wiring its `use` statements are one task, not two.
- **`plan-decomposer`: Fortran module wiring check** — three explicit rules (USE statement wiring, interface/procedure exposure, consumer co-location) plus a self-test build prompt are added to the quality checklist.

### Changed

- `fix` skill final-validation section renamed from "Build" to "Lint sweep (gfortran -Wall -Wextra)" to reflect the new flag-enabled build step.
- `enrich-phase-plan` pipeline diagram updated to show the new dry-run compile step between the reviewer loop and the architect final review.
