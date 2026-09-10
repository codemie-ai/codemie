# Technical Research

**Task**: budget spending assignment admin
**Generated**: 2026-09-09T00:00:00Z
**Research path**: filesystem

---

## 1. Original Context

Fix budget assignment to record carried-over spending at the moment of assignment in both single-user and bulk assignment paths in src/codemie/service/budget/budget_service.py. Follow the pattern the budget-reset code already uses, including its behaviour of recording nothing when the user has no earlier spending history, and make sure a failure to record it can never make the assignment itself fail.

---

## 2. Codebase Findings

### Existing Implementations

- `src/codemie/service/budget/budget_service.py` — `BudgetService` class; all assignment and reset logic lives here.
  - `assign_budget_to_user` (line 1079) — single-user path. Loads `db_user` at line 1101 (`db_user.username` is available). The `budget_id is not None` branch (line 1109) upserts the DB row, clears cache, inserts an activity event, then propagates to provider (fail-open). No carry-over recording today.
  - `bulk_set_user_budgets` (line 1166) — bulk entry point. Calls `_load_bulk_budget_users` (returns `db_users` dict keyed by `user_id`, each with `.username`), then `_persist_bulk_budget_assignments`, then `_propagate_bulk_budget_assignments`.
  - `_persist_bulk_budget_assignments` (line 1206) — persists DB rows for N users. Signature is `(session, user_ids, assignments, actor_id)` — it does **not** receive `db_users` and has no access to `username`.
  - `reset_user_budget_spending` (line 1272) — the authoritative pattern to follow. After each provider reset it reads `project_spend_tracking_repository.get_latest_by_budget_ids(session, [budget_id], username)`, constructs a `ProjectSpendTracking` marker row with `budget_period_spend=Decimal("0")`, `daily_spend=Decimal("0")`, `cumulative_spend=prev_row.cumulative_spend`, and calls `project_spend_tracking_repository.insert_budget_entries(session, marker)`. The entire block is wrapped in `try/except`; on failure it calls `session.rollback()` and logs a warning — the loop continues to the next category regardless.

- `src/codemie/repository/project_spend_tracking_repository.py` — `ProjectSpendTrackingRepository`.
  - `get_latest_by_budget_ids(session, budget_ids, project_name)` (line 539) — returns `dict[str, ProjectSpendTracking]` keyed by `budget_id`, scoped to a single `project_name` (which is the user's email/identifier). Used by the reset path.
  - `insert_budget_entries(session, rows)` (line 359) — bulk upsert with `spend_subject_type='budget'` partial index.

- `src/codemie/service/spend_tracking/spend_models.py` — `ProjectSpendTracking` SQLModel; fields: `id`, `project_name`, `user_id`, `budget_id`, `budget_category`, `spend_subject_type`, `spend_date`, `daily_spend`, `cumulative_spend`, `budget_period_spend`, `created_at`.

- `src/codemie/service/spend_tracking/spend_collector_service.py` — `LiteLLMSpendCollectorService._quantize_spend` (classmethod, line 589). Normalises a `Decimal` to 9-decimal-place precision. The reset path guards its marker write with `_quantize_spend(prev_row.budget_period_spend) > Decimal("0")`.

### Architecture and Layers Affected

- **Service layer** (`BudgetService`): primary change target — two paths inside one class.
- **Repository layer** (`project_spend_tracking_repository`): read (`get_latest_by_budget_ids`) and write (`insert_budget_entries`) calls already used by the reset path; no changes needed here.
- **Model layer** (`ProjectSpendTracking`): no changes needed; the marker row constructor is identical to the reset path.

### Integration Points

- `project_spend_tracking_repository` is a module-level singleton imported at the top of `budget_service.py` (`from codemie.repository.project_spend_tracking_repository import project_spend_tracking_repository`).
- `LiteLLMSpendCollectorService` is imported lazily inside the try block in the reset path to avoid a circular import; the assignment paths must follow the same lazy-import pattern.
- The spend delta chain for personal budgets is keyed by `(project_name, budget_id)` where `project_name` equals the user's `username` (email). This is the convention already established by the reset and spend-collector paths.

### Patterns and Conventions

- **Fail-open for ancillary writes**: every provider propagation call and the reset marker write is wrapped in `try/except Exception`; failures log a `logger.warning` and the main operation continues. The task requires the same for the new carry-over write.
- **Session rollback on tracking failure**: the reset path calls `await session.rollback()` inside the except block before continuing; this pattern must be reproduced.
- **Empty-list guard**: `insert_budget_entries` is called with a possibly-empty list; the method returns early when `rows` is empty, so the `if prev_row is not None` guard that produces the list is the only conditional logic needed.
- **Lazy import of `LiteLLMSpendCollectorService`**: done inside the try block to avoid circular import.
- **`datetime.now(timezone.utc)`** used as `spend_date` for all marker rows.

---

## 3. Documentation Findings

### Guides and Architecture Docs

- `.ai-run/guides/architecture/service-layer-patterns.md` — covers service orchestration conventions.
- `.ai-run/guides/data/database-patterns.md` — SQLModel and session patterns.
- `.ai-run/guides/testing/testing-service-patterns.md` — service test patterns directly applicable.

### Architectural Decisions

No ADR or inline decision marker directly addresses the carry-over tracking feature. The fail-open pattern is established implicitly by every analogous write in this service.

### Derived Conventions

- All ancillary tracking writes in `BudgetService` use the `try/except` + `session.rollback()` + `logger.warning` pattern — no exception propagates from them.
- `username` is the correct `project_name` for spend tracking rows on personal budgets (verified: `reset_user_budget_spending` uses `username = db_user.username` at line 1300 as `project_name` in the marker row).
- Bulk helpers (`_persist_*`, `_propagate_*`) in this service are private methods with narrow signatures; adding a parallel `_record_assignment_carry_over` helper at the same level follows the existing decomposition.

---

## 4. Testing Landscape

### Existing Coverage

- `tests/codemie/service/budget/test_budget_service.py` — three tests directly modelling the reset carry-over pattern the task references:
  - `test_reset_user_budget_spending_writes_zero_marker_row` — verifies marker row fields when prior spend exists.
  - `test_reset_user_budget_spending_no_marker_when_no_prior_row` — verifies empty marker list when no history.
  - `test_reset_user_budget_spending_rolls_back_session_on_marker_failure` — verifies `session.rollback()` is called when insert raises.
- `tests/codemie/service/budget/test_budget_assignments.py` — only covers `_validate_budget_matches_category`; no tests for `assign_budget_to_user` or the bulk path.

### Testing Framework and Patterns

- pytest + pytest-asyncio (`@pytest.mark.asyncio`).
- `unittest.mock`: `AsyncMock` for coroutines, `MagicMock` for sync objects, `patch` as context manager.
- `SimpleNamespace` for lightweight fakes (e.g. `db_user`, `prev_row`).
- `_make_budget_tracking_row` helper in `test_budget_service.py` (line 793) creates `ProjectSpendTracking` fakes for reset tests — the same helper is usable for assignment carry-over tests.

### Coverage Gaps

- `assign_budget_to_user` — no test for the carry-over marker write, nor for the fail-open behaviour when the tracking insert fails.
- `bulk_set_user_budgets` / `_persist_bulk_budget_assignments` — no tests at all for carry-over recording.
- Both gaps mirror the exact scenarios the three reset tests cover and should be filled by analogous tests.

---

## 5. Configuration and Environment

### Environment Variables

No environment variables gate the spend tracking write. `DB_INSERT_BATCH_SIZE` and `DB_IN_CLAUSE_BATCH_SIZE` control `ProjectSpendTrackingRepository` batch sizes but are irrelevant here (single-row inserts).

### Configuration Files

No feature flags or deployment configuration relevant to this change.

### Feature Flags and Deployment Concerns

None identified.

---

## 6. Risk Indicators

- **`_persist_bulk_budget_assignments` lacks `username` access.** The method signature is `(session, user_ids, assignments, actor_id)` — no `db_users` dict. `db_users` is available at the `bulk_set_user_budgets` level. Adding the carry-over recording at that level (as a separate private method, analogous to `_propagate_bulk_budget_assignments`) avoids a breaking signature change; alternatively `db_users` can be threaded through. Either approach is localised to this one call site but must be chosen deliberately.

- **Lazy import pattern for `LiteLLMSpendCollectorService` must be preserved.** Placing the import outside the try block would introduce a circular dependency visible at import time. The reset path wraps the import inside the try block exactly to avoid this.

- **`session.rollback()` must precede `continue` in any loop.** The reset path calls rollback before allowing the loop to proceed to the next category. Omitting this leaves the session in a failed state for subsequent iterations.

- **Marker rows use `spend_subject_type="budget"`.** The `insert_budget_entries` method's partial unique index and upsert logic is scoped to that subject type. Using a different value would bypass the dedup index and create duplicate rows.

- **No tests for `assign_budget_to_user` carry-over today.** The three reset tests are the direct template; their absence for the assignment path means regressions would be invisible without new tests.

---

## 7. Summary for Complexity Assessment

The change is confined to a single file (`src/codemie/service/budget/budget_service.py`) and adds one try/except block per assignment path, each following an already-established pattern from `reset_user_budget_spending` (lines 1322–1355). All required infrastructure — repository methods, model constructors, `_quantize_spend`, the lazy-import idiom — is already present and in use. The single-user path (`assign_budget_to_user`) has `db_user.username` immediately available, making it a near-direct copy of the reset block. The bulk path requires a small structural decision: `_persist_bulk_budget_assignments` does not receive `db_users`, so the carry-over write either needs `db_users` threaded in or a new private method called from `bulk_set_user_budgets` after `_persist_bulk_budget_assignments`.

Technical novelty is minimal — the pattern is fully specified by the existing reset code. The main implementation risk is the bulk-path `username` gap, which requires a deliberate approach choice but is low-risk to resolve. The fail-open requirement (`try/except` + `session.rollback()` + log warning) is identical to what the reset path already does and is well-understood.

Test coverage for the new behaviour does not exist yet. Three analogous tests in `test_budget_service.py` cover the reset path and serve as the direct template. The assignment paths need the same three scenarios — marker written when prior spend exists, nothing written when no history, session rolled back when insert fails — across both single-user and bulk paths, yielding up to six new test cases.

---

## 8. External References

None named by the task.
