# Technical Research

**Task**: project-budget member override rebalance litellm
**Generated**: 2026-10-26
**Research path**: codegraph

---

## 1. Original Context

Review https://jiraeu.epam.com/browse/EPMCDME-16071 and implement the fix (backend part only): a batch endpoint POST /v1/admin/project-budgets/member-overrides/bulk for bulk member budget overrides. The full requirements (acceptance criteria, phases, endpoint contract, intended differences) are in docs/superpowers/tasks/2026-10-26-bulk-budget-override-batch/sdlc-standard-prompt.md — Read that file first; treat it as the verbatim ticket body. UI work and the 'under 10 seconds' criterion are out of scope; the Verification section (local stack benchmarks) is not part of this run.

(Ticket body read from that file; summarized in Section 8.)

---

## 2. Codebase Findings

### Existing Implementations
- `src/codemie/rest_api/routers/project_budget_router.py` — `router = APIRouter(prefix="/v1/admin/project-budgets")` (:62). Single PATCH `override_member_allocation` (:400-424): `_require_budgeting_enabled()`, `get_async_session()`, `get_project_budget_project_name`, `_ensure_can_manage_project_budget_members` (:211, raises via `_raise_access_denied` -> `ExtendedHTTPException(403, "Access denied", details, help)`), service call, `session.commit()`, then `get_project_budget` + `_build_project_budget_response` (:90) for the response. `GET /{budget_id}` is at :290, so a literal bulk path must be declared above the `/{budget_id}` routes.
- `src/codemie/service/budget/project_budget_service.py` (`ProjectBudgetService`, singleton `project_budget_service`):
  - `override_member_allocation` :1633-1703 — get_project_budget, `_validate_member_amounts` (ValidationException -> 400), `update_member_override`, 404 `Member allocation not found for user '…'`, `_ensure_override_child_budget` (:470 -> `_upsert_child_budget`), `update_member_budget_routing` (shared_budget_id fallback `build_shared_project_budget_id`), `rebalance_project_budget`, `MEMBER_ALLOCATION_OVERRIDDEN` event (entity PROJECT_BUDGET_GROUP) + `member_allocation_override_completed` log.
  - `rebalance_project_budget` :1476-1523 — `get_project_budget`, `_resync_member_allocations`, clears `_resolution_cache` entries for (project, category), logs `project_budget_rebalance_completed`, inserts `PROJECT_BUDGET_REBALANCED` event.
  - `_resync_member_allocations` :1269 — loads allocations, `_equal_amounts_with_fixed_overrides` (ValidationException -> ExtendedHTTPException 400), `_prepare_resync_member` per member (DB writes), one `provider.sync_member_allocations(items)` call, `_persist_resync_outcomes`, `_ensure_shared_child_budget_after_resync`.
  - `_equal_amounts_with_fixed_overrides` :261 (static) — reads only `allocation_mode`, `allocated_max_budget`, `allocated_soft_budget`, `user_id`; Decimal arithmetic; raises `ValidationException` with "Personal soft/hard limits of project members cannot exceed the project soft/hard limit." messages.
  - `_validate_member_amounts` :252 (static), `get_project_budget` :1091 (404 `Project budget not found: {id}`, returns budget, assignment, allocations), `get_project_budget_project_name` :1104 (404 via `_get_active_assignment_or_404`).
- `src/codemie/repository/project_budget_repository.py` — `ProjectMemberBudgetAssignmentRepository`: `update_member_override` :776 (sets mode "fixed", amounts, `override_reason` unconditionally so None clears it, `assigned_by`; flush + refresh per call; returns None if no active row), `update_member_budget_routing` :749, `get_active_by_budget_id` :585, `update_allocation` :728.
- `src/codemie/rest_api/models/project_budget.py` — `OverrideMemberAllocationRequest` :109 (`allocated_max_budget: float = Field(ge=0)`, `allocated_soft_budget: float = Field(ge=0)`, `override_reason: Optional[str] = Field(max_length=500)`); `ProjectBudgetResponse` :71. New request/response models belong next to :109.
- Provider batch path (already on the base branch, not to be changed): `sync_member_allocations` in `src/codemie/enterprise/litellm/budget_provider_adapter.py:1468`, protocol at `src/codemie/service/budget/provider.py:377`, noop at `provider_registry.py:234`, `ProjectMemberSyncExecutor` in `src/codemie/enterprise/litellm/project_member_sync_executor.py` (workers from `config.LITELLM_PROJECT_MEMBER_SYNC_WORKERS`, shared, event-loop bound).

### Architecture and Layers Affected
Router (FastAPI, `authenticate`) -> Service (`ProjectBudgetService`) -> Repository (`project_member_budget_assignment_repository`, `budget_repository`, `activity_event_repository`) -> SQLModel tables (`budgets`, `project_budget_assignments`, `project_member_budget_assignments`) and, via `BudgetEnforcementProvider` (`get_active_provider()`), the LiteLLM enterprise adapter. API models layer: `rest_api/models/project_budget.py`.

### Integration Points
- AsyncSession is single-use per request, not concurrency-safe; router owns commit.
- `_resolution_cache` (module-level, `budget_resolution_service`) is cleared inside `rebalance_project_budget`.
- `SettingsService.get_enforce_member_spend_limits(project_name)` used in resync and `_build_project_budget_response`.
- Activity events via `activity_event_repository.async_insert(ActivityEventCreate(...), session)`.

### Patterns and Conventions
- Router endpoints: `_require_budgeting_enabled()`, `async with get_async_session() as session`, explicit `await session.commit()`, response built with `_build_project_budget_response`.
- Errors: `ExtendedHTTPException(code=..., message=...)`; `ValidationException` is converted to 400 in the service.
- Structured logs `budget_event=<name> component=project_budget_service ...`.
- Project preference (memory): no explanatory code comments.

---

## 3. Documentation Findings

### Guides and Architecture Docs
- `.ai-run/guides/api/rest-api-patterns.md` — routers stay in router modules; literal paths must precede parameterized ones (Starlette first match); errors via shared exceptions.
- `.ai-run/guides/testing/testing-api-patterns.md` — Router Contract Guards: `tests/codemie/rest_api/router_guards/` compares OpenAPI against `baselines/openapi.json` and route order against `baselines/route_precedence.json`; any new route/model fails until baselines are regenerated (`poetry run python -m tests.codemie.rest_api.router_guards.regenerate_baselines`) after review.
- `.ai-run/guides/testing/testing-service-patterns.md` — mock repositories/providers, pytest-asyncio.
- Other relevant guides present: `architecture/service-layer-patterns.md`, `data/repository-patterns.md`, `development/error-handling.md`, `development/logging-patterns.md`, `standards/git-workflow.md`, `quality-gates.md`.

### Architectural Decisions
None recorded beyond the ticket's "Intended differences" (check once per budget on final state; one rebalance event per budget; 403 fails whole request; single commit; no lock).

### Derived Conventions
Per-item results are not an existing pattern in this router; the PATCH raises on first failure.

---

## 4. Testing Landscape

### Existing Coverage
- `tests/codemie/service/budget/test_project_budget_service_lifecycle.py` — `override_member_allocation` (404 member missing, success logging), rebalance, `_equal_amounts_with_fixed_overrides`.
- `tests/codemie/service/budget/test_project_budget_allocation_rounding.py` — `_equal_amounts_with_fixed_overrides` messages and rounding, resync.
- `tests/codemie/service/budget/test_project_budget_service.py` — `get_project_budget_project_name` 404/ok, misc service paths.
- `tests/codemie/rest_api/routers/test_project_budget_router.py`, `test_project_budget_router_additional.py`, `test_project_budget_router_schema.py`, helper `project_budget_helpers.py` — router access control (`_ensure_can_manage_project_budget_members` 403), endpoints.
- `tests/codemie/repository/test_project_budget_repository.py` — `update_member_override` sets fixed, returns None when missing.
- `tests/enterprise/litellm/test_budget_provider_adapter.py`, `test_project_member_sync_executor.py`, `tests/codemie/service/budget/test_provider_registry.py` — batch sync path.

### Testing Framework and Patterns
pytest + pytest-asyncio (async tests, `ProjectBudgetService()` instantiated directly with patched repositories/provider). Router tests via client/handler with patched module-level names in the router module.

### Coverage Gaps
No tests exist for a bulk path (none could). Router OpenAPI/route-order baselines will need regeneration.

---

## 5. Configuration and Environment

### Environment Variables
- `LITELLM_PROJECT_MEMBER_SYNC_WORKERS` (`config`) — executor size, stated unchanged by the ticket.
- `BUDGET_STOP_ENABLED` and scheduler cron vars exist in the budget area but are unrelated to this endpoint.

### Configuration Files
- `pyproject.toml:58` pins `codemie-enterprise` 2.3.44; ticket says local editable 2.3.45 and `compose.enterprise-local.yaml` are local overrides not to be committed.
- `tests/codemie/rest_api/router_guards/baselines/openapi.json`, `route_precedence.json` — committed contract baselines.

### Feature Flags and Deployment Concerns
`_require_budgeting_enabled()` is a no-op (enforcement may be noop provider). Enterprise/LiteLLM is optional; noop provider `sync_member_allocations` loops per item.

---

## 6. Risk Indicators

- Phase 1 reads allocations through the same AsyncSession that returns attached ORM rows; mutating them would autoflush partial state (ticket mandates lightweight copies). `_equal_amounts_with_fixed_overrides` only needs four attributes.
- `update_member_override` does flush + refresh per item; `rebalance_project_budget` re-fetches via `get_project_budget`; repeated (budget, user) items and identity-map behaviour in unit tests with mocked sessions need care.
- Route ordering: `/member-overrides/bulk` vs `/{budget_id}/...` routes; `route_precedence.json` and `openapi.json` guards will fail until baselines are intentionally regenerated.
- Whole-request rollback semantics: the session context in `get_async_session` and the router's single commit must guarantee rollback on Phase 3 400 or unexpected errors (not verified here).
- Per-budget 404 handling vs whole-request 403 ordering: permission failure must occur before any write, across all distinct budgets.
- Speculative: the response `budgets` rebuild after commit may need session expiry handling (`expire_on_commit`) since the PATCH calls `get_project_budget` after commit; reuse that pattern.
- Speculative: an item cap of 1000 with many budgets means sequential rebalances (each re-fetches allocations and syncs to LiteLLM); latency scales with number of distinct budgets.
- No existing multi-status response pattern in this router; test coverage for the new contract must be written from scratch.

---

## 7. Summary for Complexity Assessment

The change adds one router endpoint, request/response models, and one new service method orchestrating existing building blocks (`_validate_member_amounts`, `update_member_override`, `_ensure_override_child_budget`, `update_member_budget_routing`, `rebalance_project_budget`, the `MEMBER_ALLOCATION_OVERRIDDEN` audit insert). It touches the API-model, router and service layers; repository, provider, adapter and executor are explicitly untouched. Expected file surface is small: three source files plus tests and regenerated router-guard baselines (openapi.json, possibly route_precedence.json).

Technical novelty is moderate: the pattern (per-item checks, a final-state fixed-sum check on lightweight copies, writes, one rebalance per budget, audit) is fully specified by the ticket and reuses existing, well-tested helpers. The novel parts are the per-item result model, ORM-safe check-then-write phasing, and single-transaction rollback semantics. Everything needed already exists on the base branch (batch LiteLLM sync).

Test posture: the underlying pieces have solid unit coverage, but there is none for bulk behaviour. Main risks are ORM identity-map/autoflush interactions, route ordering and OpenAPI baselines, and transaction rollback on Phase 3 failure.

---

## 8. External References

- `docs/superpowers/tasks/2026-10-26-bulk-budget-override-batch/sdlc-standard-prompt.md` — resolved. Key facts:
  - Endpoint `POST /v1/admin/project-budgets/member-overrides/bulk`, declared above `/{budget_id}` routes; `authenticate` + `_require_budgeting_enabled()`.
  - Request `{items: [{budget_id, user_id, allocated_max_budget, allocated_soft_budget, override_reason?}]}`, 1-1000 items, no dedupe/reorder; later accepted item wins for repeated (budget, user); each accepted item gets own result and audit event. Reuse field constraints of `OverrideMemberAllocationRequest`.
  - Response 200 `{results: [{budget_id, user_id, status: "ok"|"failed", code?, message?}], budgets: [ProjectBudgetResponse]}`; results same order as input; `budgets` only for budgets with an accepted item, in first-appearance order, built after commit via `get_project_budget` + `_build_project_budget_response`.
  - Router per distinct budget (first-appearance order): `get_project_budget_project_name` once (404 -> all items of that budget fail with that 404, others continue) and `_ensure_can_manage_project_budget_members` once (403 -> whole request fails before any write).
  - Service `bulk_override_member_allocations`: Phase 1 checks without writes or LiteLLM calls (per item: budget exists, `_validate_member_amounts` 400, member exists 404 `Member allocation not found for user '…'`; per budget: apply accepted items to lightweight copies, call `_equal_amounts_with_fixed_overrides` once, on failure all remaining items of that budget get 400 with its message); Phase 2 writes in input order with same args as `override_member_allocation` :1649-1677; Phase 3 `rebalance_project_budget` once per touched budget, sequential, unmodified, 400 re-check failure not caught and rolls back all; Phase 4 per-item `MEMBER_ALLOCATION_OVERRIDDEN` event + `member_allocation_override_completed` log as :1681-1703. Router commits once.
  - Keep PATCH and `override_member_allocation` unchanged. No changes to `rebalance_project_budget`, adapter, executor, `LITELLM_PROJECT_MEMBER_SYNC_WORKERS`, enterprise package. Quirks kept: omitted/null `override_reason` clears; every accepted item makes member FIXED.
  - Out of scope: locking, UI, categories in parallel, bulk clear/reset, Verification benchmarks.
  - Git: new branch from `EPMCDME-14996_batch-member-budget-sync` per `.ai-run/guides/standards/git-workflow.md` (ticket EPMCDME-16071); do not push or open an MR without asking. (Current branch observed: `EPMCDME-16071_bulk-member-budget-override-batch`.)
- Jira URL https://jiraeu.epam.com/browse/EPMCDME-16071 — not fetched; the task says to treat the local file as the verbatim ticket body.
