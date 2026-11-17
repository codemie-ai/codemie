# Technical Research

**Task**: cost center allocated budget project budget member allocation rebalance project membership
**Generated**: 2026-10-26
**Research path**: codegraph

---

## 1. Original Context

Jira EPMCDME-16356 (Sub-Bug of EPMCDME-15623 Cost Center Rollup Card). Cost Center page shows incorrect Allocated Budget numbers after adding/removing a user from a project linked to that cost center. Manually running the project budget rebalance action corrects them, so cost-center-level figures are not recalculated on project membership change. Expected: after add/remove user on a linked project, cost center Allocated Budget reflects updated allocation automatically. Regression: projects without membership changes stay unaffected.

---

## 2. Codebase Findings

### Existing Implementations
- `src/codemie/service/budget/cost_center_budget_summary.py` — `get_cost_center_budget_summary(async_session, project_names)` builds the cost center rollup. `allocated_member_budget_total` per category is summed in `_RowAggregation.add_category` (key `"allocated"`) from rows returned by `project_budget_assignment_repository.get_assigned_budget_summaries_for_projects` (`src/codemie/repository/project_budget_repository.py:180`, reads `ProjectMemberBudgetAssignment`). It is computed at read time; there is no stored cost-center aggregate. So the figure is only as fresh as the stored per-member `allocated_soft_budget` / `allocated_max_budget` rows.
- `src/codemie/rest_api/routers/cost_centers.py` — `get_cost_center_detail` and the budget rollup endpoint (`get_cost_center_budget_rollup`, used in tests) call the summary above.
- `src/codemie/service/project/project_assignment_service.py` — membership change entry points:
  - `assign_user_to_project` (:358) calls `_sync_project_budget_member_added` (:209). New `ProjectMemberBudgetAssignment` (EQUAL mode, `SyncStatus.PENDING`) gets amounts copied from the first existing equal-mode allocation via `_get_member_added_allocation_amounts` (:181), else the full project budget soft/max. Other members' allocations are NOT recomputed.
  - `remove_user_from_project` (:749) and `bulk_remove_users_from_project` call `_sync_project_budget_member_removed` (:268): provider delete, then soft-delete (`deleted_at`) the member's allocations. Remaining members' shares are NOT recomputed.
  - Routers: `src/codemie/rest_api/routers/projects/membership.py` (assign, update role, delete, bulk assign/remove, CSV import via `csv_import_service.assign_from_csv`). Sync `Session`, commit in the router.
- `src/codemie/service/budget/project_budget_service.py` (async `AsyncSession`):
  - `rebalance_project_budget(session, budget_id, actor_id)` (:1355) calls `_resync_member_allocations` (:1184: `_equal_amounts_with_fixed_overrides`, per-member `_resync_member_allocation`, `_ensure_shared_child_budget_after_resync`), clears `_resolution_cache` for the project/category, writes `PROJECT_BUDGET_REBALANCED` activity event. Exposed at `POST /v1/admin/project-budgets/{budget_id}/rebalance` (`project_budget_router.py:381`, maintainer only).
  - `rebalance_project_budget_group` (:2463) loops assignments.
  - `recompute_equal_shares(session, project_name)` (:1233): DB-only recompute of equal shares for all budgets of a project, no provider calls; caller commits. Callers: `src/codemie/enterprise/litellm/project_member_runtime_sync.py` (`resync_project_member_allocations`, `_recompute`).
- Observed root cause candidate (reported fact, not design): the membership paths never invoke `rebalance_project_budget` / `recompute_equal_shares`, which are the code that re-splits equal shares; the manual rebalance is the only trigger.

### Architecture and Layers Affected
Router (`rest_api/routers/projects/membership.py`, `project_budget_router.py`, `cost_centers.py`) -> service (`ProjectAssignmentService`, `ProjectBudgetService`, `cost_center_budget_summary`) -> repository (`project_budget_repository.py`, `application_repository`) -> models (`budget_models.py`: `Budget`, `ProjectBudgetAssignment`, `ProjectMemberBudgetAssignment`; `user_management.UserProject`). Plus enterprise LiteLLM provider layer (`budget_provider_adapter.py`, `project_member_runtime_sync.py`).

### Integration Points
- Budget provider (`get_active_provider()`): rebalance syncs through it; membership removal already calls `provider.delete_member_allocation` via `_run_budget_provider_coro` (sync-to-async bridge in the sync service).
- `_resolution_cache` / `clear_budget_resolution_cache` in `budget_resolution_service`; removal pops `(project, category, user)` key.
- `ActivityEventCreate` / `activity_event_repository` for audit events.
- Cost center link: `Application.cost_center_id`; `application_repository.list_projects_by_cost_center_id`, `aget_project_names_by_cost_center_name`.

### Patterns and Conventions
- Static methods on `ProjectAssignmentService`; sync `Session` there vs. `AsyncSession` in budget services (sync/async mismatch is bridged by `_run_budget_provider_coro`).
- Services flush, routers commit.
- Structured `budget_event=... component=...` log lines.
- Provider failures on membership paths are logged and swallowed (fail-open).

---

## 3. Documentation Findings

### Guides and Architecture Docs
No budget-specific guide found. Generic: `.ai-run/guides/architecture/service-layer-patterns.md`, `layered-architecture.md`, `.ai-run/guides/data/repository-patterns.md`, `testing/testing-service-patterns.md` (per AGENTS.md; not read in detail).

### Architectural Decisions
Docstring of `get_cost_center_budget_summary`: pure-async, takes resolved project names, never touches a sync Session. `_sync_project_budget_member_added` docstring says "conservative allocations".

### Derived Conventions
Per-member allocation rows are the source of truth for the rollup; rollup sums them live.

---

## 4. Testing Landscape

### Existing Coverage
- `tests/codemie/service/project/test_project_assignment_service.py` — assign/remove/bulk-remove, spend capture on removal (`test_bulk_removal_captures_spend_per_allocation`, `test_allocation_soft_deleted_after_capture_and_delete`, `test_spend_capture_failure_is_fail_open`).
- `tests/codemie/rest_api/routers/test_projects_router.py` — membership routes.
- `tests/codemie/service/budget/test_cost_center_budget_summary.py`, `tests/codemie/rest_api/routers/test_cost_centers_router.py` — rollup.
- `tests/codemie/service/budget/test_project_budget_service.py`, `test_project_budget_allocation_rounding.py` (recompute_equal_shares), `tests/enterprise/litellm/test_project_member_runtime_sync.py`.
- `tests/codemie/rest_api/routers/test_project_budget_router.py` (+ `_additional`), `tests/codemie/repository/test_project_budget_repository.py`.

### Testing Framework and Patterns
pytest, `pytest.mark.asyncio`, `AsyncMock`/`MagicMock`/`SimpleNamespace` for sessions and providers, `patch` on module attributes. Version not checked.

### Coverage Gaps
No test asserts that other members' shares change after add/remove. `ActiveProjectBudgetRestoreService` has no tests within 3 hops (adjacent, not in scope).

---

## 5. Configuration and Environment

### Environment Variables
`BUDGET_STOP_ENABLED`, `INACTIVE_PROJECT_BUDGET_STOP_SCHEDULE`, `INACTIVE_COST_CENTER_BUDGET_STOP_SCHEDULE`, `ACTIVE_PROJECT_BUDGET_RESTORE_SCHEDULE` (schedulers only, adjacent). `ENABLE_USER_MANAGEMENT` gates cost center and membership routers.

### Configuration Files
`config.COST_CENTER_NAME_PATTERN` in `codemie.configs`. Project setting `SettingsService.get_enforce_member_spend_limits(project)` alters rebalance (effective max budget).

### Feature Flags and Deployment Concerns
`customer_config.is_feature_enabled("costCenters")`; budgeting gate `_require_budgeting_enabled()` in `project_budget_router.py`. Scheduler leader locks 987654321-328 registry (comments in scheduler files).

---

## 6. Risk Indicators

- Sync vs async: membership service is sync `Session`; `rebalance_project_budget` / `recompute_equal_shares` are async `AsyncSession` with provider calls. Existing bridge `_run_budget_provider_coro` is the in-repo precedent.
- Bulk and CSV paths (`bulk_remove_users_from_project`, `bulk_assign`, `csv_import_service.assign_from_csv`) share the same helpers; any hook has to cover all of them or the symptom persists on those routes.
- Provider side effects: full rebalance pushes to the LiteLLM provider; `recompute_equal_shares` is DB-only. Pending `SyncStatus` rows are created on add.
- Add path copies a sibling's share rather than recomputing, so totals can exceed or undershoot the project budget until rebalance (observed behavior).
- `project_member_runtime_sync.py` already recomputes shares on a different trigger; possible overlap/double work.
- Projects without budgets short-circuit (`if not assignments: return`); regression requirement for unaffected projects is naturally testable there.
- Speculative: the fix is likely a recompute call from the membership service after add/remove; whether it should include provider sync is a spec decision.
- Speculative: cost center UI may cache; no frontend code in this repo was examined.
- No budget-specific guide exists.

---

## 7. Summary for Complexity Assessment

The Allocated Budget on the cost center page is computed live in `cost_center_budget_summary.py` by summing per-member `ProjectMemberBudgetAssignment` rows. There is no stored cost-center aggregate. The membership paths in `ProjectAssignmentService` (add, remove, bulk remove, CSV import) only create or soft-delete the individual member row. They never re-split equal shares among remaining members, which `rebalance_project_budget` (POST rebalance) and `recompute_equal_shares` do. The layers touched are the membership service and possibly the budget service. The cost center summary and routers look unaffected from the code read. Likely surface is 1-3 source files plus tests.

Novelty is low, since the recompute logic exists, but the sync/async boundary and provider sync semantics add moderate care. Test posture is good for assignment service and budget services, with the specific "other members' shares change after membership change" behavior untested.

Key risks: covering every membership entry point, the sync/async bridge, provider-sync side effects, and the overlap with `project_member_runtime_sync`.

---

## 8. External References

None named by the task.
