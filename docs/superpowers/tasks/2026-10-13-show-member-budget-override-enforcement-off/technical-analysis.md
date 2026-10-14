# Technical Research

**Task**: project budget member allocation override enforce_member_spend_limits
**Generated**: 2026-10-13
**Research path**: filesystem

---

## 1. Original Context

Ticket EPMCDME-15764 — "A member budget override reports success, but the displayed allocation amounts do not change when Enforce member spend limits is off."

Agreed scope ("show it only"): With the project setting "Enforce member spend limits" OFF, show a member's saved budget override amount in the project member Budget Allocations instead of the full project budget. Display only — no change to what is enforced at the provider (LiteLLM) and no change to runtime sync.

Today `_build_project_budget_response` and `list_project_budget_members` in src/codemie/rest_api/routers/project_budget_router.py return `budget.max_budget` as `allocated_max_budget` for every member when enforcement is off (introduced by EPMCDME-12959, commit f91b192f7; spec at docs/superpowers/tasks/2026-06-22-epmcdme-12959-budget-stale-allocation/spec.md). So a saved override (ProjectMemberBudgetAssignment.allocation_mode == "fixed", written by ProjectBudgetService.override_member_allocation in src/codemie/service/budget/project_budget_service.py) reports success and shows the "Overridden" marker in the UI, but its amount is never displayed.

Required change: on the API read path, with enforcement off, members whose allocation_mode is "fixed" return their stored `allocated_max_budget`; non-overridden (equal-mode) members keep returning `budget.max_budget`. Keep `allocated_member_budget_total` consistent with the per-member values returned. Behaviour with enforcement ON is unchanged. No changes to project_member_runtime_sync.py or the LiteLLM adapter. Add regression tests for the enforcement-off read path covering fixed and equal allocations (existing tests live in tests/codemie/rest_api/routers/test_project_budget_router.py).

Acceptance criteria:
1. Enforcement off + member has a fixed override → both `GET /v1/admin/project-budgets/{id}/members` and every response built by `_build_project_budget_response` (list/get/override/clear-override/rebalance) return the stored override amount as that member's `allocated_max_budget`.
2. Enforcement off + member without override → `allocated_max_budget == budget.max_budget` (unchanged from today).
3. Enforcement on → persisted `allocated_max_budget` for all members (unchanged).
4. `allocated_member_budget_total` equals the sum of the per-member values returned.
5. Provider sync / runtime enforcement behaviour unchanged.
6. Regression tests cover 1–4.

Also check and report (do not change): any other read paths that compute a per-member limit with the same `enforce_limit` rule and could show an inconsistent number next to the changed one — notably src/codemie/service/analytics/handlers/member_spend_service.py (around line 85) and src/codemie/rest_api/routers/analytics/spending.py — and whether `allocated_soft_budget` needs any handling.

---

## 2. Codebase Findings

### Existing Implementations

`src/codemie/rest_api/routers/project_budget_router.py` holds the three places the rule appears on the read path, all as the same inline ternary `a.allocated_max_budget if enforce_limit else budget.max_budget`:

- line 79–82, `_build_project_budget_response`: reads `SettingsService.get_enforce_member_spend_limits(project_name)` and computes `allocated_total` (returned as `allocated_member_budget_total`, line 97).
- line 109, same function: per-member `allocated_max_budget` inside `member_allocations`.
- line 348–355, `list_project_budget_members` (`GET /v1/admin/project-budgets/{budget_id}/members`): its own copy of the ternary; it does not go through the helper and returns no total (`ProjectBudgetMembersResponse` has only `data`).

Both sites already emit `allocation_mode=a.allocation_mode` and `allocated_soft_budget=a.allocated_soft_budget` straight from the row.

Callers of `_build_project_budget_response`: `get_project_budget` (283), `override_member_allocation` (406), `clear_member_override` (429) directly; `create` (236), `list` (269), `update` (305), `reset` (334), `rebalance` (379) through `_load_and_build_response` (118–122).

Write side, `src/codemie/service/budget/project_budget_service.py`:

- `override_member_allocation` (1432–1502) → `project_member_budget_assignment_repository.update_member_override` (`src/codemie/repository/project_budget_repository.py:723–751`) sets `allocation_mode = "fixed"` and stores both amounts on the row, independent of the enforcement flag; then `rebalance_project_budget` (1275).
- `_equal_amounts_with_fixed_overrides` (246–273): fixed rows keep their stored amounts; equal rows get `(budget − Σfixed) / n_equal`. Raises `ValidationException("fixed overrides exceed project budget")` when Σfixed exceeds the budget, so a stored override is never above `budget.max_budget`.
- `_validate_member_amounts` (237–243): `max >= 0`, `soft >= 0`, `soft <= max`. An override of `0` is valid.
- `clear_member_override` (1504–) flips the row back to `AllocationMode.EQUAL`.

Model and enum: `ProjectMemberBudgetAssignment` (`src/codemie/service/budget/budget_models.py:291–`) — `allocation_mode` (default `equal`), `allocated_soft_budget`, `allocated_max_budget`, `override_budget_id`, `effective_budget_id`. `AllocationMode` (`src/codemie/service/budget/budget_enums.py:31–33`) has exactly two members, `EQUAL = "equal"` and `FIXED = "fixed"`; the router already imports it. Note `ProjectBudgetAssignment.allocation_mode` (budget level, surfaced as the top-level `allocation_mode`) is a different field from the per-member one.

Response models (`src/codemie/rest_api/models/project_budget.py:60–94, 114`): `ProjectBudgetMemberAllocationResponse`, `ProjectBudgetResponse`, `ProjectBudgetMembersResponse`.

### Architecture and Layers Affected

- **API/router**: `project_budget_router.py` — the only layer where the read-path rule lives.
- **Service / repository / provider**: read from, not part of the rule. `SettingsService.get_enforce_member_spend_limits` (`src/codemie/service/settings/settings.py:1136–1153`) is a synchronous settings lookup returning `False` for an empty project name or a missing setting.

### Integration Points

Other places a per-member limit or member total is produced:

| Path | Rule applied | Surface |
|---|---|---|
| `src/codemie/service/analytics/handlers/member_spend_service.py:77–85` `_limit_value` | none — always persisted `allocation.allocated_max_budget`; file has no `enforce_limit` reference | `{category}_limit` columns of `GET /v1/analytics/project-member-spending` and `/user-project-spending` |
| `src/codemie/rest_api/routers/analytics/spending.py:263–344` | computes no limit; delegates to `member_spend_service` (lines 301, 338) | same two endpoints |
| `project_budget_router.py:458` `_build_project_budget_group_response` | none — `sum(a.allocated_max_budget ...)` | `allocated_member_budget_total` per category on `/v1/admin/project-budget-groups` |
| `src/codemie/repository/project_budget_repository.py:253` → `src/codemie/rest_api/routers/projects/crud.py:163` | none — SQL `SUM(allocated_max_budget)` | `ProjectAssignedBudgetSummary.allocated_member_budget_total` on project list/detail |

So with enforcement off the analytics limit already differs from the admin budget API today: an equal member shows the persisted equal share in analytics and `budget.max_budget` in the admin API; a fixed member shows the stored override in analytics and `budget.max_budget` in the admin API.

The `enforce_limit` ternary also appears on write/sync paths only (not read): `project_budget_service.py:529, 951, 1196, 1911, 2293`; `src/codemie/service/budget/budget_service.py:1476, 1565`; `src/codemie/enterprise/litellm/project_member_runtime_sync.py:185, 291`.

Provider behaviour for a fixed member with enforcement off, as coded:

- Override/rebalance path: `_resync_member_allocation` (1104–1144) routes FIXED rows to `_sync_fixed_allocation` (1064–1102), which calls `provider.sync_member_allocation(allocation, budget)` with no `effective_max_budget`; the adapter (`src/codemie/enterprise/litellm/budget_provider_adapter.py:1162–1164`) then uses `allocation.allocated_max_budget` — the override amount.
- Runtime sync (`project_member_runtime_sync.py:185`) and the toggle resync (`:291`) use `budget.max_budget` for every allocation when enforcement is off, with no branch on `allocation_mode`.

### Patterns and Conventions

- Router helpers are module-level private functions (`_build_...`, `_member_budget_id`, `_allocation_value`); the settings lookup is done once per response, before the comprehension.
- Per-member mode comparisons elsewhere use `AllocationMode.FIXED.value` against the string column (`project_budget_service.py:251, 1127`).
- Lines 80–81 carry an inline comment stating that with enforcement disabled "each member holds the full budget independently, so the aggregate can exceed the project cap".

---

## 3. Documentation Findings

### Guides and Architecture Docs

`.ai-run/guides/` exists; no guide covers project budgets or the enforcement flag (a grep for `budget|enforce` hits five unrelated guides once each). Generic guides that apply: `.ai-run/guides/api/rest-api-patterns.md`, `.ai-run/guides/testing/testing-api-patterns.md`, `.ai-run/guides/testing/testing-patterns.md`, `.ai-run/guides/quality-gates.md` (not opened for this research). `docs/` has no design doc for the setting outside `docs/superpowers/tasks/`.

### Architectural Decisions

- EPMCDME-12959 spec (see Section 8) introduced the current rule and recorded: "`allocated_soft_budget` is not changed — soft limits are not governed by the enforcement flag." Its code review lists this as AC-7, passed.
- `ProjectMemberBudgetAssignment` docstring: `provider_metadata` is opaque and core must not branch on its contents.
- `AGENTS.md`: tests are written only when explicitly requested (this ticket requests them); the repository has no CI pipeline config.

### Derived Conventions

Router tests build `Budget`/assignment/allocation as `SimpleNamespace` objects and patch `codemie.rest_api.routers.project_budget_router.SettingsService.get_enforce_member_spend_limits`.

---

## 4. Testing Landscape

### Existing Coverage

`tests/codemie/rest_api/routers/test_project_budget_router.py`:

- `test_build_project_budget_response_uses_full_budget_when_enforcement_disabled` (94) — two `equal` rows at 50, budget 100 → each 100, total 200.
- `test_build_project_budget_response_uses_allocated_budget_when_enforcement_enabled` (142) — same rows → each 50, total 100.
- `test_list_project_budget_members_returns_effective_budget_when_enforcement_disabled` (224) — one `equal` row → 100.
- `test_list_project_budget_members_returns_nullable_budget_id` (191), `test_build_project_budget_response_includes_member_budget_id` (58) — enforcement patched `True`.
- Member override/clear endpoint tests (650–912) use `_patch_member_endpoint`, which hard-patches enforcement to `True` and returns an empty allocation list from `_budget_lookup`; they assert access control and call counts, not amounts.

`tests/codemie/rest_api/routers/test_project_budget_router_additional.py:94` `test_clear_member_override_returns_reloaded_budget_response` — the only router test with a `fixed` row (max 15, budget 25). It does not patch `SettingsService` and asserts only `budget_id` and `allocation_mode`.

`tests/codemie/service/analytics/handlers/test_member_spend_service.py` (87–122, 313–351, 789–819) pins `_limit_value` to the persisted `allocated_max_budget`. `tests/enterprise/litellm/test_project_member_runtime_sync.py` covers the runtime rule.

### Testing Framework and Patterns

pytest 8 with `pytest-asyncio`, `pytest-xdist` (`pytest.ini`: `-n 3`, `--import-mode=importlib`, `pythonpath = src`). `@pytest.mark.asyncio` on endpoint coroutines called directly; `mock_session_ctx` and user builders (`admin_user`, `maintainer_user`, `project_admin_user`, ...) come from `tests/codemie/rest_api/routers/project_budget_helpers.py`; `get_async_session` and `project_budget_service.get_project_budget` are patched with `AsyncMock`.

### Coverage Gaps

- No test combines enforcement off with a `fixed` allocation, in either `_build_project_budget_response` or `list_project_budget_members`.
- No test mixes `fixed` and `equal` rows, so the total over mixed per-member values is unasserted.
- No test asserts `allocated_soft_budget` in the enforcement-off path (flagged in the EPMCDME-12959 code review).
- Override/clear endpoint tests never exercise enforcement off.

---

## 5. Configuration and Environment

### Environment Variables

None in the feature area. The flag is a per-project setting, not an env var.

### Configuration Files

`enforce_member_spend_limits` is stored as a project-scoped setting (alias `SettingsService.ENFORCE_MEMBER_SPEND_LIMITS_ALIAS`, `CredentialTypes.ENVIRONMENT_VARS`, key `ENABLED`), read by `get_enforce_member_spend_limits` and written by `set_enforce_member_spend_limits` (`settings.py:1136–1166`). Exposed on project models (`src/codemie/rest_api/models/project.py:173, 199, 213`) and toggled through `ProjectService` (`src/codemie/service/project/project_service.py:262–280`), which triggers `resync_project_member_allocations`.

### Feature Flags and Deployment Concerns

No feature flag gates the read path. No DB tables or migrations are involved in the read path. `tests/codemie/rest_api/router_guards/baselines/openapi.json` snapshots the API schema; the response models carry the affected fields already.

---

## 6. Risk Indicators

- **Two copies of the rule.** `_build_project_budget_response` (lines 82 and 109) and `list_project_budget_members` (line 355) each inline the ternary; three expressions must stay in agreement for AC 1 and AC 4. Speculative: a shared per-member helper would remove the drift risk.
- **Displayed amount vs. provider amount for fixed members with enforcement off.** The override path pushes the stored override to LiteLLM (`_sync_fixed_allocation`), while runtime sync and the toggle resync push `budget.max_budget` for every row. Which value is live at the provider depends on the last sync that ran; whether a later runtime sync re-fires depends on the `enforce_limit` key in the row's `provider_metadata`, which `_sync_fixed_allocation` does not set explicitly (not traced further). The shown override therefore may or may not equal the enforced cap — accepted by the "show it only" scope, but worth stating in the spec.
- **Analytics stays partly inconsistent.** `member_spend_service._limit_value` applies no enforcement rule. Speculative: after this change fixed members will agree between the admin API and the analytics `{category}_limit` columns, while equal members will still differ (persisted equal share vs. `budget.max_budget`), as they do today.
- **Totals on other surfaces.** `allocated_member_budget_total` on project-budget-groups (`project_budget_router.py:458`) and on the project list (`project_budget_repository.py:253`) is the persisted sum, already different from the total this endpoint reports with enforcement off.
- **`allocated_soft_budget`.** Returned from the row on both paths regardless of enforcement, so a fixed member's stored soft override is already displayed; `soft <= max` is enforced at write time, so soft cannot exceed the stored max. Speculative: no handling is needed for fixed members. For equal members the persisted equal-share soft value is shown beside `budget.max_budget` — existing behaviour, unchanged.
- **Zero override.** `allocated_max_budget = 0` is a valid override; a truthiness check instead of an `allocation_mode` check would misreport it.
- **Stale inline comment.** Lines 80–81 describe every member as holding the full budget when enforcement is off.
- **Unpatched settings lookup in a neighbouring test.** `test_clear_member_override_returns_reloaded_budget_response` runs a `fixed` row through the real `SettingsService`; its assertions are amount-agnostic today.

---

## 7. Summary for Complexity Assessment

The change sits in one layer and one file: the API router `src/codemie/rest_api/routers/project_budget_router.py`, where the enforcement-off rule is inlined three times across `_build_project_budget_response` (per-member value and total) and `list_project_budget_members`. Everything the rule needs is already loaded at those sites — the allocation rows carry `allocation_mode`, `AllocationMode` is imported, and the enforcement flag is already fetched. No service, repository, model, schema, migration, provider-adapter or runtime-sync code participates in the read path, and the response models already expose the fields involved.

Nothing here is technically novel; it refines a rule added by EPMCDME-12959 using a field the same responses already return. The write side guarantees a stored override never exceeds the project budget and that soft never exceeds max, so `allocated_soft_budget` is already correct for overridden members.

Test posture is good for the existing rule (three enforcement tests in `tests/codemie/rest_api/routers/test_project_budget_router.py` with an established `SimpleNamespace` + patch pattern) but has no case for enforcement off with a `fixed` row or with mixed modes. The main risks are consistency rather than complexity: keeping the three expressions aligned, the displayed override possibly differing from the cap live at LiteLLM, and analytics/group/project-list surfaces that use the persisted amounts with no enforcement rule and so remain inconsistent for equal-mode members.

---

## 8. External References

- `docs/superpowers/tasks/2026-06-22-epmcdme-12959-budget-stale-allocation/spec.md` — resolved (inside this repository). Facts used:
  - Problem it fixed: with enforcement disabled the API returned the persisted split; "every member should show `100` and the aggregate should show `200`" for a 100 budget with two members.
  - Rule it introduced, mirrored from runtime sync: `effective_max_budget = allocation.allocated_max_budget if enforce_limit else budget.max_budget`, applied "to both per-member values and the aggregate".
  - Acceptance criterion now being narrowed: "When `enforce_member_spend_limits` is disabled, `GET /v1/admin/project-budgets/{id}/members` returns `allocated_max_budget = budget.max_budget` for every member and `allocated_member_budget_total = N × budget.max_budget`."
  - "`allocated_soft_budget` is not changed — soft limits are not governed by the enforcement flag."
  - It makes no mention of `fixed` allocations or overrides.
- Commit `f91b192f7` ("EPMCDME-12959: Fix stale member budget allocation responses after disabling enforce_member_spend_limits") — resolved in local history; it added the router change and the task artefacts in that directory.
