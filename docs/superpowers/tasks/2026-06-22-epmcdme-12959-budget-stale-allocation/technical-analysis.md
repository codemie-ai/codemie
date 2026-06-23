# Technical Research

**Task**: project budget member allocation enforce_member_spend_limits
**Generated**: 2026-06-22T00:00:00Z
**Research path**: codegraph

---

## 1. Original Context

Member budget allocations display stale split values after disabling enforce_member_spend_limits.

When a project has enforce_member_spend_limits enabled and member budgets are rebalanced, disabling enforce_member_spend_limits via the project settings UI causes member budget allocation API responses to continue showing the old split values instead of the full project budget per member.

For example, when a project has a total budget of 100 and two members, enabling enforce_member_spend_limits and triggering a rebalance assigns each member 50. After disabling enforce_member_spend_limits, each member should effectively have access to the full project budget of 100. However, the API read path still returns allocation.allocated_max_budget from the database, which remains at the previous rebalanced value of 50.

Relevant code:
- project_member_runtime_sync.py:184, 290 — effective_max_budget = allocation.allocated_max_budget if enforce_limit else budget.max_budget
- project_budget_router.py:178 — _build_project_budget_response returns allocated_max_budget=a.allocated_max_budget
- project_budget_router.py:392 — list_project_budget_members returns allocated_max_budget=a.allocated_max_budget

The fix: The API read path should apply the effective budget calculation (using enforce_member_spend_limits flag) when building responses, same way the runtime sync does.

Acceptance criteria:
- When enforce_member_spend_limits is disabled, member budget allocation API responses display the full project budget for each member.
- When enforce_member_spend_limits is enabled, member budget allocation API responses display the persisted per-member allocation value.
- GET /v1/admin/project-budgets/{id}/members returns allocation values reflecting effective budget behavior.
- _build_project_budget_response returns allocation values reflecting effective budget behavior.
- LiteLLM synchronization behavior remains unchanged.
- Regression coverage is added for toggling enforce_member_spend_limits from enabled to disabled after a rebalance.

---

## 2. Codebase Findings

### Existing Implementations

- `src/codemie/rest_api/routers/project_budget_router.py` — FastAPI router for all project budget endpoints. Contains the two sites that must change:
  - `_build_project_budget_response` (line 136): builds `ProjectBudgetResponse` including `member_allocations`; uses `a.allocated_max_budget` verbatim for each member.
  - `list_project_budget_members` (line 366): `GET /{budget_id}/members`; also uses `a.allocated_max_budget` verbatim without checking the enforcement flag.
  - `_load_and_build_response` (line 177): async helper used by create, update, reset, rebalance; delegates to `_build_project_budget_response` — this path will also benefit from the fix automatically.
  - `get_project_budget` endpoint (line 299): calls `_build_project_budget_response` directly after `project_budget_service.get_project_budget`.
  - `override_member_allocation` and `clear_member_override` (lines 410, 436): also call `_build_project_budget_response` directly.

- `src/codemie/enterprise/litellm/project_member_runtime_sync.py` — The reference implementation of the effective budget calculation:
  - `ensure_project_member_runtime_ready` (line 93): line 183 calls `SettingsService.get_enforce_member_spend_limits(project_name)`, then line 184 computes `effective_max_budget = allocation.allocated_max_budget if enforce_limit else budget.max_budget`.
  - `resync_project_member_allocations` (line 266): line 290 applies the same formula when re-syncing all members after a flag toggle.

- `src/codemie/service/settings/settings.py` — `SettingsService.get_enforce_member_spend_limits(project_name: str) -> bool` (line 835): reads the persisted enforcement flag from a project-scoped `ENVIRONMENT_VARS` credential setting. Returns `False` when the setting is absent.

- `src/codemie/service/budget/budget_models.py` — `ProjectBudgetAssignment` (line 179): holds `project_name` and `allocation_mode`; does NOT hold the enforcement flag. `ProjectMemberBudgetAssignment` (line 218): holds `allocated_max_budget`, `allocated_soft_budget`, and `allocation_mode` — the persisted values that become stale.

- `src/codemie/service/budget/project_budget_service.py` — `get_project_budget` (line 850): returns `(budget, assignment, allocations)` — the budget object is available there, providing `budget.max_budget` needed for the effective calculation.

### Architecture and Layers Affected

- **API layer** (`src/codemie/rest_api/routers/project_budget_router.py`): the two response-building sites need to apply the effective budget formula. This is the primary change surface.
- **Service layer** (`src/codemie/service/settings/settings.py`): `SettingsService.get_enforce_member_spend_limits` is already present and used by the runtime sync; it must now also be called from the router's read path.
- **No database layer changes**: the `ProjectMemberBudgetAssignment` table rows are intentionally left at their persisted rebalanced values. The fix is purely in the response-projection logic, not in persistence.

### Integration Points

- `SettingsService.get_enforce_member_spend_limits(project_name)` is a synchronous class method call. The router is async; the call is fine inside an async handler (it is not I/O — it reads from the in-process settings store).
- `project_budget_service.get_project_budget` returns the `assignment` object, which carries `assignment.project_name` — needed to call `get_enforce_member_spend_limits`. Both `_build_project_budget_response` (receives `assignment`) and `list_project_budget_members` (fetches the `assignment` from `get_project_budget`) already have access to `project_name`.
- The `budget` object (carries `budget.max_budget`) is also available in both call sites: `_build_project_budget_response` receives `budget` as a parameter; `list_project_budget_members` receives `_budget` from `get_project_budget` but currently discards it.

### Patterns and Conventions

- The effective budget formula is already established in the runtime sync:
  ```python
  enforce_limit = SettingsService.get_enforce_member_spend_limits(project_name)
  effective_max_budget = allocation.allocated_max_budget if enforce_limit else budget.max_budget
  ```
  The router fix should replicate this pattern exactly.
- Helper functions in the router (`_build_project_budget_response`, `_member_budget_id`) follow the pattern of receiving domain model objects directly and projecting them to Pydantic response models. The change fits the existing pattern: pass `project_name` and `budget.max_budget` so the helper can compute the effective value.
- `_build_project_budget_response` currently also computes `allocated_total = sum(a.allocated_max_budget for a in allocations)` for `allocated_member_budget_total`. Whether this aggregate should also use the effective value is a design decision not explicitly required by the acceptance criteria, but worth considering for consistency.

---

## 3. Documentation Findings

### Guides and Architecture Docs

- `.ai-run/guides/architecture/layered-architecture.md` — covers where logic belongs; confirms router helpers are the right place for response projection.
- `.ai-run/guides/api/rest-api-patterns.md` — FastAPI router patterns.
- `.ai-run/guides/data/repository-patterns.md` — describes how repository results flow to response models.

### Architectural Decisions

- The separation between `allocated_max_budget` (persisted, rebalanced split) and the effective runtime budget (determined by the enforcement flag) is intentional. The DB row is the source of truth for what was last explicitly rebalanced; the enforcement flag governs whether that split or the full budget is surfaced. This is exactly the pattern in the runtime sync and must now be extended to the read API.

### Derived Conventions

- Synchronous `SettingsService` calls are used inside async FastAPI handlers in other routers (see other router files); this is safe.
- Response builder helpers receive domain objects and perform projection inline; they are not async. If `get_enforce_member_spend_limits` must be called, it should be called before invoking the helper and passed in, or the helper signature should be extended.

---

## 4. Testing Landscape

### Existing Coverage

- `tests/codemie/rest_api/routers/test_project_budget_router.py`:
  - `test_build_project_budget_response_includes_member_budget_id` — unit test for `_build_project_budget_response`; checks `budget_id` on the allocation response but does NOT test `allocated_max_budget` effective value.
  - `test_list_project_budget_members_returns_nullable_budget_id` — tests `list_project_budget_members` with a fixed `allocated_max_budget=25.0`; patches `project_budget_service.get_project_budget` but does NOT patch `SettingsService.get_enforce_member_spend_limits`.
  - `test_project_admin_can_read_budget_members_for_owned_project` — access control test; does not test allocation values.
  - No test currently covers the scenario: enforce flag disabled + stale DB value → response shows full project budget.

- `tests/enterprise/litellm/test_project_member_runtime_sync.py`:
  - `test_runtime_sync_passes_full_project_budget_when_enforcement_disabled` — directly tests the formula in the sync layer; passes `enforce_limit=False`, expects `effective_max_budget=budget.max_budget`. This is the closest analogue for the new router tests to follow.
  - `test_syncs_member_allocation_and_persists_runtime_metadata` — tests enforce=True case.

### Testing Framework and Patterns

- Framework: `pytest` with `pytest-asyncio` (`@pytest.mark.asyncio`).
- Async handlers tested by calling them directly with mocked dependencies (no test client needed).
- `unittest.mock.patch` for patching `get_async_session` via `asynccontextmanager` mock, patching `project_budget_service.*` via `AsyncMock`, patching `SettingsService.get_enforce_member_spend_limits` via `return_value=True/False`.
- `SimpleNamespace` used to construct lightweight fakes for `Budget`, `ProjectBudgetAssignment`, `ProjectMemberBudgetAssignment`.

### Coverage Gaps

- No test for `_build_project_budget_response` when `enforce_member_spend_limits=False` — should return `budget.max_budget` instead of `allocation.allocated_max_budget`.
- No test for `_build_project_budget_response` when `enforce_member_spend_limits=True` — should return `allocation.allocated_max_budget`.
- No test for `list_project_budget_members` endpoint checking the value of `allocated_max_budget` under either flag state.
- New regression test required: toggle from enabled (rebalanced to 50) to disabled → response shows 100 (full budget).

---

## 5. Configuration and Environment

### Environment Variables

- `ENFORCE_MEMBER_SPEND_LIMITS_ALIAS` — the alias key used by `SettingsService` to look up the per-project enforcement flag. Stored as a project-scoped `ENVIRONMENT_VARS` credential. No new env vars are needed for the fix.

### Configuration Files

- The enforcement flag is stored in the `settings` table via `SettingsService.upsert_project_setting` with `CredentialTypes.ENVIRONMENT_VARS`. Reading it requires no config file changes.

### Feature Flags and Deployment Concerns

- No new feature flags or deployment changes required. The fix is purely in the response projection path. LiteLLM sync behavior (`resync_project_member_allocations`, `ensure_project_member_runtime_ready`) is unchanged — the acceptance criteria explicitly requires this.

---

## 6. Risk Indicators

- `_build_project_budget_response` is a pure synchronous helper that currently takes `(budget, assignment, allocations)`. To apply the effective formula it needs `project_name` (from `assignment.project_name`) and `budget.max_budget` (already on `budget`). Since both are already parameters, no signature change is required for the allocation value calculation itself — only the logic inside the list comprehension changes. However, calling `SettingsService.get_enforce_member_spend_limits` inside a sync helper is fine but must be done before the comprehension loop, not per-member.
- `list_project_budget_members` currently discards `_budget` from `get_project_budget`. The fix requires that the budget object be retained so `budget.max_budget` is accessible when computing the effective value.
- `allocated_member_budget_total` in `ProjectBudgetResponse` is also computed from `a.allocated_max_budget`. If the flag is disabled this aggregate will still reflect the old split totals, not the effective total. This is a related inconsistency not called out in the acceptance criteria, but should be noted.
- `SettingsService.get_enforce_member_spend_limits` is synchronous and reads from the DB/settings store. Calling it in every `_build_project_budget_response` invocation (including the list endpoint that pages through budgets) introduces a settings lookup per budget. This is consistent with how it is already called in the runtime sync path, but may add latency for large lists. The existing call pattern in other routers confirms this is acceptable.
- Test file `test_project_budget_router.py` patches `get_async_session` at the router module level. The new test for `_build_project_budget_response` is a unit test that doesn't need session mocking — only a `SettingsService` patch — which matches the pattern in `test_project_member_runtime_sync.py`.
- No migration is required; no DB schema changes.

---

## 7. Summary for Complexity Assessment

The task is a focused read-path fix confined to two sites in a single router file (`src/codemie/rest_api/routers/project_budget_router.py`). The effective budget formula (`allocated_max_budget if enforce_limit else budget.max_budget`) is already implemented in the runtime sync layer and has established test coverage there. The router fix is a mechanical application of the same formula to the response projection layer. Total file change surface: one router file (2–3 lines of logic change to the helper and one endpoint, plus a new `SettingsService` import and call), and one test file (2–3 new test functions). No database schema changes, no migration, no new abstractions.

The task follows an entirely established pattern. `SettingsService.get_enforce_member_spend_limits` already exists, is already tested in the runtime sync, and is already called synchronously from async handlers in the codebase. The `ProjectBudgetAssignment.project_name` and `Budget.max_budget` values are already available at both call sites; the only structural adjustment needed in `list_project_budget_members` is to stop discarding the `_budget` return value from `get_project_budget`.

Test coverage for the affected router is thin on the allocation-value correctness dimension: existing tests check presence and shape but not the effective value under different flag states. Two new unit tests for `_build_project_budget_response` (flag=True and flag=False) and one integration-style test for `list_project_budget_members` (flag=False after rebalance) satisfy the regression coverage acceptance criterion. Testing patterns from `test_project_member_runtime_sync.py` provide a direct template.
