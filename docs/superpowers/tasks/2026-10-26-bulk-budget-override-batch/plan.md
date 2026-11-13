# Bulk Member Budget Override Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `POST /v1/admin/project-budgets/member-overrides/bulk` that applies many member overrides with one rebalance per budget.

**Architecture:** New request/response models, one new service method `bulk_override_member_allocations` (check, write, re-check, rebalance, audit phases) composed of existing helpers, and a router endpoint that does per-budget 404/403 handling and commits once. Repository gains one optional parameter.

**Tech Stack:** FastAPI, SQLModel/AsyncSession, pytest + pytest-asyncio.

**Spec:** `docs/superpowers/tasks/2026-10-26-bulk-budget-override-batch/spec.md` (analysis: `technical-analysis.md` in the same dir)

Commit per task using the repository's existing convention (`EPMCDME-16071: Description`). No code comments in new code. Tests follow `test_project_budget_service_lifecycle.py` (service) and `test_project_budget_router.py` (router) patterns.

## Global Constraints

- Items 1 to 1000 (422 otherwise); amounts `float, ge=0`; `override_reason` optional, `max_length=500`.
- PATCH endpoint, `override_member_allocation`, `rebalance_project_budget`, adapter, executor, enterprise package unchanged.
- Phase 1 performs no writes, no provider calls, no ORM row mutation (lightweight copies only).
- Single `session.commit()` in the router; no explicit rollback code.

## Review Focus

- Repeated (budget, user) in one request: later accepted item wins, each gets a result and an event (Task 4).
- Item whose member is missing next to valid items: 404 result, others succeed (Task 3).
- All items failing: 200, `budgets: []`, no writes/rebalance/events (Tasks 4, 5).
- Stored amounts differ from Phase 1 objects (concurrent commit): 400, no rebalance (Task 4).
- Missing budget for some items only: those items 404, others processed (Task 5).

## Negative-constraint pass

Honored: Phase 1 no writes (T3); `rebalance_project_budget` unmodified and called once per budget (T4); no locking, no LiteLLM compensation, no UI, no new repo changes beyond `populate_existing` (T1); PATCH unchanged (T5); baselines regenerated, not hand-edited (T6).

---

### Task 1: `populate_existing` on `get_active_by_budget_id`

**Files:**
- Modify: `src/codemie/repository/project_budget_repository.py:585-596`
- Test: `tests/codemie/repository/test_project_budget_repository.py`

**Interfaces:**
- Produces: `get_active_by_budget_id(self, session, budget_id: str, populate_existing: bool = False) -> list[ProjectMemberBudgetAssignment]`

Test-first: yes — the default call's statement lacks the `populate_existing` execution option; with `populate_existing=True` it carries it (fails today: unexpected kwarg).

- [ ] **Step 1: Write failing tests** `test_get_active_by_budget_id_default_does_not_populate_existing` and `test_get_active_by_budget_id_populate_existing_sets_execution_option`: capture the statement passed to a mocked `session.execute`; assert `stmt.get_execution_options().get("populate_existing")` is falsy by default and `True` when requested.
- [ ] **Step 2: Run** `poetry run pytest tests/codemie/repository/test_project_budget_repository.py -k get_active_by_budget_id -v`; expect FAIL.
- [ ] **Step 3: Implement** the parameter; when true, `stmt = stmt.execution_options(populate_existing=True)`. Default path identical to today.
- [ ] **Step 4: Re-run**; expect PASS. Commit.

### Task 2: Bulk request/response models

**Files:**
- Modify: `src/codemie/rest_api/models/project_budget.py` (after `OverrideMemberAllocationRequest`, :109-112)
- Test: `tests/codemie/rest_api/routers/test_project_budget_router_schema.py`

**Interfaces:**
- Produces (all `BaseModel`):
  - `BulkMemberOverrideItem(budget_id: str, user_id: str, allocated_max_budget: float = Field(ge=0), allocated_soft_budget: float = Field(ge=0), override_reason: Optional[str] = Field(default=None, max_length=500))`
  - `BulkMemberOverrideRequest(items: list[BulkMemberOverrideItem] = Field(min_length=1, max_length=1000))`
  - `BulkMemberOverrideResult(budget_id: str, user_id: str, status: Literal["ok", "failed"], code: Optional[int] = None, message: Optional[str] = None)`
  - `BulkMemberOverrideResponse(results: list[BulkMemberOverrideResult], budgets: list[ProjectBudgetResponse])`

Test-first: yes — a request with 0 items, 1001 items or a negative amount raises `ValidationError` (fails today: models do not exist).

- [ ] **Step 1: Write failing tests** `test_bulk_request_rejects_empty_items`, `..._rejects_1001_items`, `..._rejects_negative_amounts`, `..._accepts_1000_items_with_null_reason`, each asserting `pydantic.ValidationError` or success.
- [ ] **Step 2: Run** the new tests; expect FAIL (ImportError).
- [ ] **Step 3: Implement** the four models as specified above.
- [ ] **Step 4: Re-run**; expect PASS. Commit.

### Task 3: Service Phase 1 (checks without writes)

**Files:**
- Modify: `src/codemie/service/budget/project_budget_service.py` (new method next to `override_member_allocation`, :1633)
- Test: `tests/codemie/service/budget/test_project_budget_service_bulk_override.py` (create)

**Interfaces:**
- Consumes: `BulkMemberOverrideItem`, `BulkMemberOverrideResult` (Task 2); existing `get_project_budget`, `_validate_member_amounts`, `_equal_amounts_with_fixed_overrides`.
- Produces: `async def _bulk_check_overrides(self, session: AsyncSession, items: list[tuple[int, BulkMemberOverrideItem]]) -> tuple[dict[int, BulkMemberOverrideResult], list[tuple[int, BulkMemberOverrideItem]]]` returning failed results by input index, and accepted `(index, item)` pairs in input order.

Test-first: yes — a soft-above-max item yields a failed 400 result while a valid sibling is accepted (fails today: method does not exist).

Behavior: load each distinct budget once via `get_project_budget` (404 `Project budget not found: {id}` becomes failed results for its items); per item run `_validate_member_amounts` (400, today's message) and check member exists in loaded allocations (404 `Member allocation not found for user '{user_id}'`); per budget build `types.SimpleNamespace` copies (`user_id`, `allocation_mode`, `allocated_max_budget`, `allocated_soft_budget`), apply passing items in order (later wins, mode `AllocationMode.FIXED.value`), call `_equal_amounts_with_fixed_overrides(copies, budget.max_budget, budget.soft_budget)` once; on `ValidationException` every passing item of that budget becomes failed 400 with `str(exc)`.

- [ ] **Step 1: Write failing tests** with patched repositories: `test_soft_above_max_fails_400_others_ok`, `test_missing_member_fails_404_with_message`, `test_missing_budget_fails_404_all_items`, `test_order_dependent_example_accepts_all_three` (final state within limit), `test_final_state_over_limit_fails_all_remaining_400_with_message`, `test_phase1_does_not_mutate_orm_rows_or_write` (allocations unchanged; repository write methods and provider not called).
- [ ] **Step 2: Run** `poetry run pytest tests/codemie/service/budget/test_project_budget_service_bulk_override.py -v`; expect FAIL.
- [ ] **Step 3: Implement** `_bulk_check_overrides` per the behavior above.
- [ ] **Step 4: Re-run**; expect PASS. Commit.

### Task 4: Service Phases 2 to 4 (`bulk_override_member_allocations`)

**Files:**
- Modify: `src/codemie/service/budget/project_budget_service.py`
- Test: `tests/codemie/service/budget/test_project_budget_service_bulk_override.py`

**Interfaces:**
- Consumes: `_bulk_check_overrides` (Task 3), `get_active_by_budget_id(..., populate_existing=True)` (Task 1).
- Produces: `async def bulk_override_member_allocations(self, session: AsyncSession, items: list[tuple[int, BulkMemberOverrideItem]], actor_id: str) -> dict[int, BulkMemberOverrideResult]` returning a result for every input index (accepted ones `status="ok"`).

Test-first: yes — with two touched budgets, `rebalance_project_budget` is called exactly once per budget and one `MEMBER_ALLOCATION_OVERRIDDEN` event per accepted item (fails today: method does not exist).

Behavior: if nothing accepted, return after Phase 1. Phase 2: per accepted item in input order, same calls and args as `override_member_allocation` :1649-1677 (`update_member_override`, `_ensure_override_child_budget`, `update_member_budget_routing`). Phase 2b: `await session.flush()`; per touched budget (first-appearance order) reload with `populate_existing=True`, `await session.refresh(budget)`, call `_equal_amounts_with_fixed_overrides(allocations, budget.max_budget, budget.soft_budget)`; `ValidationException` becomes `ExtendedHTTPException(code=400, message=str(exc))`, raised before any rebalance. Phase 3: `rebalance_project_budget(session, budget_id, actor_id)` once per touched budget, sequential. Phase 4: per accepted item in input order, `MEMBER_ALLOCATION_OVERRIDDEN` event and `member_allocation_override_completed` log exactly as :1681-1703. Leave `override_member_allocation` untouched.

- [ ] **Step 1: Write failing tests** `test_writes_use_same_args_as_patch_and_null_reason_clears_and_sets_fixed`, `test_repeated_user_later_wins_each_item_gets_event`, `test_rebalance_once_per_touched_budget_none_for_failed_budget`, `test_recheck_runs_once_per_touched_budget_with_populate_existing_and_flush_first`, `test_recheck_failure_second_budget_raises_400_no_rebalance_no_provider`, `test_reloaded_amounts_differ_from_phase1_objects_raises_400_no_rebalance`, `test_event_and_log_only_for_accepted_items`, `test_all_items_fail_returns_all_failed_no_writes_rebalance_provider_events`.
- [ ] **Step 2: Run** the file; expect FAIL.
- [ ] **Step 3: Implement** the method per behavior above.
- [ ] **Step 4: Re-run**; expect PASS. Commit.

### Task 5: Router endpoint

**Files:**
- Modify: `src/codemie/rest_api/routers/project_budget_router.py` (new route after `GET ""` at :257, before `GET /{budget_id}` at :290; extend model imports at :29)
- Test: `tests/codemie/rest_api/routers/test_project_budget_router.py`

**Interfaces:**
- Consumes: Task 2 models; `project_budget_service.bulk_override_member_allocations` (Task 4).
- Produces: `@router.post("/member-overrides/bulk", response_model=BulkMemberOverrideResponse)` handler `bulk_override_member_allocations(payload: BulkMemberOverrideRequest, user: User = Depends(authenticate))`.

Test-first: yes — a 403 from the permission helper on any budget returns 403 and the service is never called (fails today: route does not exist).

Behavior: `_require_budgeting_enabled()`; inside `get_async_session()`: for each distinct `budget_id` in first-appearance order call `get_project_budget_project_name` once (`ExtendedHTTPException` 404 becomes failed results `code=404`, `message` from it, budget skipped) then `_ensure_can_manage_project_budget_members(user, project_name, budget_id)` once (403 propagates, no writes); send remaining `(index, item)` pairs to the service; merge results in input order; `await session.commit()`; build `budgets` via `get_project_budget` + `_build_project_budget_response` once per budget with at least one `ok` result, first-appearance order.

- [ ] **Step 1: Write failing tests** (patched service and helpers in router module): `test_bulk_returns_results_in_input_order_and_touched_budgets`, `test_bulk_permission_denied_returns_403_and_service_not_called`, `test_bulk_missing_budget_items_failed_404_others_processed`, `test_bulk_all_failed_returns_200_empty_budgets`, `test_bulk_commits_once`, `test_bulk_422_for_zero_items_over_1000_and_negative_amounts`.
- [ ] **Step 2: Run** `poetry run pytest tests/codemie/rest_api/routers/test_project_budget_router.py -k bulk -v`; expect FAIL.
- [ ] **Step 3: Implement** the endpoint per behavior above.
- [ ] **Step 4: Re-run**; expect PASS. Commit.

### Task 6: Regenerate router-guard baselines

**Files:**
- Modify (generated): `tests/codemie/rest_api/router_guards/baselines/openapi.json`
- Verify unchanged: `tests/codemie/rest_api/router_guards/baselines/route_precedence.json`

Test-first: no — generated artifact, validated by the existing router-guard tests.

`route_precedence.json` records only same-method overlapping route pairs (`route_overlap.py:136`). The new `POST /member-overrides/bulk` overlaps no other POST route (`/{budget_id}/reset` and `/{budget_id}/rebalance` end in different literals), so only `openapi.json` is expected to change.

- [ ] **Step 1:** Run `poetry run python -m tests.codemie.rest_api.router_guards.regenerate_baselines` (do not hand-edit either baseline), then `poetry run pytest tests/codemie/rest_api/router_guards -v`; expect PASS.
- [ ] **Step 2:** Inspect `git diff` on the baselines directory: `openapi.json` adds only the new route and the four new schemas; `route_precedence.json` shows no diff. If `route_precedence.json` changed, stop and investigate before committing. Commit.

### Review follow-up: Preserve response budget order after an item failure

- Add `test_bulk_budgets_follow_first_appearance_when_first_item_fails`, parameterized for per-item 400 and 404 failures. Input `[A/failed, B/ok, A/ok, C/failed]` must keep results in input order, return budgets `[A, B]`, and fetch each returned budget once without fetching C.
- Determine successful budget IDs from results, then filter distinct budget IDs in original input order. A failed first item must not move its budget behind a later budget when another item for that budget succeeds.
- Run the regression against the original implementation to confirm the ordering failure, then run the router tests after the fix. The response schema and router-guard baselines do not change.
