# Technical Research

**Task**: project budget spend override soft limit hard limit validation
**Generated**: 2026-10-05
**Research path**: codegraph

---

## 1. Original Context

Jira EPMCDME-15600 (Bug): "Unclear error message when personal spend override soft limit exceeds project soft limit".
Description: An unclear error message is shown when a Maintainer/Project Admin overrides a user's personal spend with a soft limit higher than the project's soft limit. The existing response message is "fixed overrides exceed project budget". Expected: override rejected with a clear error message explaining the personal soft limit cannot exceed the project's soft limit. This is NOT a request to allow a higher personal soft limit.
Acceptance criteria:
1. When a personal spend override soft limit exceeds the project's soft limit, the override is rejected with a clear error message identifying the conflicting limits.
2. The error message does not misleadingly describe this case as a generic project budget violation.
3. Valid personal spend overrides continue to save successfully.
4. Other invalid limits and genuine project budget constraint violations continue to be rejected with appropriate error messages.
Extra user instruction: Error messages must be clear and user friendly for BOTH hard limit and soft limit exceedance.
Related recent commit: EPMCDME-15234 "Allow Project Admins to override member spend allocations" and EPMCDME-15048 (project budget redistribution).

---

## 2. Codebase Findings

### Existing Implementations
Request flow for the override (all paths under `/Users/bohdan_maliar/Projects/codemie-dev/codemie/`):
- `src/codemie/rest_api/routers/project_budget_router.py:462` `PATCH /v1/admin/project-budgets/{budget_id}/members/{user_id}` -> `override_member_allocation`. Body `OverrideMemberAllocationRequest` (line 138): `allocated_max_budget` ge=0, `allocated_soft_budget` ge=0, `override_reason` max 500. Access check `_ensure_can_manage_project_budget_members` (maintainer or project admin, line 273). Commits only after service returns.
- `src/codemie/service/budget/project_budget_service.py:1432` `ProjectBudgetService.override_member_allocation`. Order of operations: `get_project_budget` -> `_validate_member_amounts` (400 via `ExtendedHTTPException`) -> `update_member_override` (repo flush, sets mode `fixed`) -> `_ensure_override_child_budget` (creates/updates child budget, provider-facing) -> `update_member_budget_routing` -> `rebalance_project_budget` -> activity event.
- `project_budget_service.py:237` `_validate_member_amounts`: only checks `max >= 0`, `soft >= 0`, `soft <= max` with messages "allocated_max_budget must be >= 0", "allocated_soft_budget must be >= 0", "allocated_soft_budget must be <= allocated_max_budget". It does NOT compare against the project budget.
- `project_budget_service.py:1275` `rebalance_project_budget` -> `_resync_member_allocations` (line 1168) with `eff_max=budget.max_budget`, `eff_soft=budget.soft_budget`. It wraps `ValidationException` from the allocator into `ExtendedHTTPException(code=400, message=str(exc))` (line 1182-1184).
- **Source of the reported message**: `project_budget_service.py:246-262` `_equal_amounts_with_fixed_overrides`. It sums `allocated_max_budget` and `allocated_soft_budget` over ALL members with `allocation_mode == fixed` and does one combined check `if fixed_max > dec_max or fixed_soft > dec_soft: raise ValidationException("fixed overrides exceed project budget")`. So one message covers: single override soft > project soft, single override max > project max, and the sum of several overrides exceeding either project total. The message does not say which limit (hard/soft), the values, or the user.
- The same allocator/message is reached from `update_project_budget` (line 1207, via `_resync_member_allocations` at line 1247) when a project budget is lowered below existing fixed overrides, and from `rebalance_project_budget` (router `POST /{budget_id}/rebalance`, plus group rebalance).
- Other related validators: `_validate_constraints` (line 105: "max_budget must be > 0", "soft_budget must be >= 0", "soft_budget must be <= max_budget", duration regex, category) used for project-level budgets; `_validate_allocation_mode` (line 314).
- `src/codemie/service/budget/budget_models.py` `Budget` (soft_budget, max_budget), `ProjectMemberBudgetAssignment` (line 291; allocated_soft_budget, allocated_max_budget, allocation_mode, override_reason).
- `src/codemie/repository/project_budget_repository.py:723` `update_member_override` (flushes, no commit).
- `src/codemie/service/budget/budget_enums.py` `AllocationMode` (EQUAL, FIXED).

### Architecture and Layers Affected
- API layer: `project_budget_router.py` (request schema, authorization, session/commit boundary).
- Service layer: `ProjectBudgetService` (validation, allocation math, provider orchestration).
- Repository layer: `project_budget_repository.py` (member allocation rows; no change implied by the symptom).
- Error-handling layer: `src/codemie/rest_api/main.py:1135` handlers. `ValidationException` -> HTTP 400 `{"error": {"message", "details": null, "help": null}}`; `ExtendedHTTPException` (`core/exceptions.py:59`) -> its `code`, with `message`, `details`, `help` fields (`main.py:1151`). `ExtendedHTTPException` supports `details` and `help`, which the allocator path currently leaves unset.

### Integration Points
- `get_active_provider()` / `BudgetEnforcementProvider.sync_member_allocation` (enterprise LiteLLM adapter, `src/codemie/enterprise/litellm/`), `_ensure_override_child_budget` / `_sync_fixed_allocation` in the service.
- `SettingsService.get_enforce_member_spend_limits(project_name)` (`service/settings/settings.py:1122`) governs whether the allocated max is enforced per member.
- Activity events (`activity_repository.async_insert`, `BudgetManagementEvent.MEMBER_ALLOCATION_OVERRIDDEN`).
- Resolution cache `budget_resolution_service._resolution_cache` invalidated in rebalance.

### Patterns and Conventions
- Domain validators raise `ValidationException`; service callers convert to `ExtendedHTTPException(code=400, message=str(exc))` (three sites: `override_member_allocation`, `_resync_member_allocations`, `update_project_budget`).
- Static helper methods on `ProjectBudgetService` with `Decimal` arithmetic (`_CENT` quantization) for money.
- Logging uses `budget_event=... component=project_budget_service key=value` format.

---

## 3. Documentation Findings

### Guides and Architecture Docs
- `.ai-run/guides/development/error-handling.md` (read): section "Accurate Error Details" says a message must describe the failure that actually happened; one code path with several failure reasons must branch and produce a distinct message per reason; avoid one catch-all message for unrelated failure modes. Directly applicable to the combined hard/soft/sum check.
- `.ai-run/guides/testing/testing-service-patterns.md` (read): mock repositories/providers, use pytest-asyncio.
- Other relevant guides exist (not read in detail): `.ai-run/guides/api/endpoint-conventions.md`, `.ai-run/guides/architecture/service-layer-patterns.md`, `.ai-run/guides/standards/code-quality.md`, `.ai-run/guides/quality-gates.md`.
- No guide specific to project budget member allocations was found.

### Architectural Decisions
None recorded for allocation validation. Inline docstrings only.

### Derived Conventions
Messages for limits elsewhere are terse snake_case field names ("soft_budget must be <= max_budget"); user-facing 404/422 messages in the same file (e.g. `_ensure_project_is_active`) are full sentences naming the project.

---

## 4. Testing Landscape

### Existing Coverage
- `tests/codemie/service/budget/test_project_budget_service_lifecycle.py:510` `test_resync_member_allocations_raises_400_when_fixed_overrides_exceed_project_budget`: one fixed allocation (soft 21, max 26) vs project (soft 20, max 25); asserts `code == 400` and `message == "fixed overrides exceed project budget"` (exact string at line 536). This test pins the current message and exercises the scenario where both limits are exceeded at once.
- Same file: `test_override_member_allocation_raises_404_when_member_missing` (line 401), `test_override_member_allocation_logs_on_success` (line 429), `test_resync_member_allocation_syncs_litellm_for_equal_mode_members` (line 766).
- `tests/codemie/service/budget/test_project_budget_service.py`: `test_resync_member_allocations_updates_shared_child_budget_for_equal_members` (line 30), `test_resync_member_allocations_respects_enforce_limit` (line 214).
- `tests/codemie/rest_api/routers/test_project_budget_router.py` and `test_project_budget_router_additional.py`: router-level tests including override endpoint access.
- `tests/codemie/repository/test_project_budget_repository.py`: includes `update_member_override` (`test_update_member_override_sets_fixed_mode`).

### Testing Framework and Patterns
pytest with pytest-asyncio; `AsyncMock` session, `SimpleNamespace` stand-ins for allocations/budgets, `patch("codemie.service.budget.project_budget_service.project_member_budget_assignment_repository...")`.

### Coverage Gaps
- No test found for `_equal_amounts_with_fixed_overrides` directly with only-soft exceeded, only-max exceeded, or multi-override sum cases.
- No test for the happy-path override within limits at the `_equal_amounts_with_fixed_overrides` level (AC3), nor for `_validate_member_amounts` messages.
- No test that `override_member_allocation` with an over-limit soft value fails end to end (service + handler response shape).

---

## 5. Configuration and Environment

### Environment Variables
None specific to this validation. Budget scheduler settings (`BUDGET_STOP_ENABLED`, `INACTIVE_PROJECT_BUDGET_STOP_SCHEDULE`, `ACTIVE_PROJECT_BUDGET_RESTORE_SCHEDULE`) are unrelated.

### Configuration Files
- Per-project setting "enforce member spend limits" via `SettingsService.get_enforce_member_spend_limits` (`service/settings/settings.py:1122`); affects whether the per-member max is enforced, not the validation.
- `config.LLM_PROXY_BUDGET_CHECK_ENABLED` gates only the global `budget_router`, not `project_budget_router` (`main.py:1022-1025`).

### Feature Flags and Deployment Concerns
`_require_budgeting_enabled()` in the router is a no-op. No migration is implied by what exists today.

---

## 6. Risk Indicators

- Single shared check in `_equal_amounts_with_fixed_overrides` (`project_budget_service.py:261`) is reached from three flows (member override, project budget update, rebalance); message wording must make sense in each context (override of one member vs. project total lowered below existing overrides).
- Validation occurs AFTER side effects in `override_member_allocation`: `update_member_override` flush and `_ensure_override_child_budget` (provider-facing) run before `rebalance_project_budget` raises. The router commits only on success, so DB rows roll back with the session, but any provider-side child budget change made before the failure is not obviously rolled back. Not verified; worth checking `get_async_session` rollback semantics (`src/codemie/clients/postgres.py:273`) and `_ensure_override_child_budget`.
- The check compares aggregates across all fixed members, so the failing override may belong to a different user than the one being edited (existing overrides already consume the budget). A per-user message ("your soft limit X exceeds project soft limit Y") would be wrong in that case; hard-limit and soft-limit and aggregate cases are three distinct failure modes.
- When both max and soft are exceeded (as in the existing test), only one message can be raised unless the code reports both.
- Existing test at `test_project_budget_service_lifecycle.py:536` asserts the exact old string; it will fail on any message change and must be revisited.
- Exception type differs by site: allocator raises `ValidationException`; callers re-wrap to `ExtendedHTTPException(400)` which can carry `details`/`help`; the `ValidationException` handler returns `details: null`. Frontend consumption of the message string is not visible from this repo.
- Float/Decimal: comparisons use `Decimal(str(float))`; messages interpolating values need consistent formatting (currency/precision not defined anywhere found).
- `_validate_member_amounts` (override) has no project-limit awareness; the project-limit check lives only in the rebalance path. Related ticket EPMCDME-15234 (project admins may override) widened the set of callers who can hit this.
- Speculative: a fix could keep one allocator and branch the message by which limit (hard, soft, or both, individual vs aggregate) is exceeded, or add an early per-member check in `override_member_allocation`. Choice between these is a design decision for spec/plan.
- Speculative: wording for hard-limit exceedance should be updated together with soft-limit per the extra instruction, so the existing test string changes.

---

## 7. Summary for Complexity Assessment

The bug is localized to the project budget member-override path: router `project_budget_router.py` (PATCH members/{user_id}) -> `ProjectBudgetService.override_member_allocation` -> `rebalance_project_budget` -> `_resync_member_allocations` -> `_equal_amounts_with_fixed_overrides`, where a single combined check on summed fixed max and soft values raises the generic "fixed overrides exceed project budget". Existing code surface is essentially one service file (`project_budget_service.py`, one static method plus possibly one validation helper and two re-wrap sites) with one existing test file that pins the old string. Layers touched: service layer and error-response shaping; the router, repository and models show no structural involvement.

Technical novelty is low: it follows the existing `ValidationException` -> `ExtendedHTTPException(400)` pattern and the `error-handling.md` guidance on distinct messages per failure reason. Complexity comes from semantics rather than volume: the check is aggregate over all fixed overrides and is shared by three flows (override, project update, rebalance), so correct wording for individual vs total exceedance of both hard and soft limits must be settled, and the existing exact-string test must change.

Test posture: the service has solid unit tests in `test_project_budget_service_lifecycle.py` and `test_project_budget_service.py`, but the allocator itself has only one directly relevant test (both limits exceeded at once). Gaps exist for soft-only, hard-only, multi-override-sum and valid-override cases. Key risks are provider-side effects occurring before validation in `override_member_allocation` and messages being wrong when the offending override belongs to a different member.

---

## 8. External References

None named by the task.
