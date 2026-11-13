# EPMCDME-16071: Bulk member budget override endpoint (backend)

Source of truth for behavior: `sdlc-standard-prompt.md` in this directory (verbatim ticket), plus the pre-rebalance re-check added at the spec gate. This spec pins scope, shapes and acceptance criteria.

## Why

The members page saves a bulk override as one `PATCH /{budget_id}/members/{user_id}` per user per category. Each PATCH runs `override_member_allocation` (`project_budget_service.py:1633-1703`), which calls `rebalance_project_budget` (:1476) over every member of the category and syncs all of them to LiteLLM. Cost is users x categories x members. The rebalance result depends only on the final fixed overrides, so one rebalance per touched budget yields the same end state.

## Scope

Add `POST /v1/admin/project-budgets/member-overrides/bulk`. Touches four source files plus tests and baselines:

- `rest_api/models/project_budget.py`, next to `OverrideMemberAllocationRequest` (:109): new request and response models.
- `rest_api/routers/project_budget_router.py`: new endpoint, declared above the `/{budget_id}` routes (GET at :290). Uses `authenticate` and `_require_budgeting_enabled()`.
- `service/budget/project_budget_service.py`: new `bulk_override_member_allocations`.
- `repository/project_budget_repository.py`: `ProjectMemberBudgetAssignmentRepository.get_active_by_budget_id` (:585) gets an optional `populate_existing: bool = False` parameter that adds `.execution_options(populate_existing=True)` to the select. Default behavior is unchanged for every existing caller.
- `tests/codemie/rest_api/router_guards/baselines/` (`openapi.json`, `route_precedence.json`): regenerated deliberately, not hand-edited.

## Data shapes (new)

- Item: `budget_id`, `user_id`, `allocated_max_budget` (float, ge 0), `allocated_soft_budget` (float, ge 0), `override_reason` (optional, max 500). Same constraints as the PATCH request.
- Request: `items`, 1 to 1000 entries (422 otherwise). No dedupe, no reorder.
- Result: `budget_id`, `user_id`, `status` ("ok" or "failed"), `code` and `message` only on failure.
- Response 200: `results` (one per input item, input order) and `budgets` (list of `ProjectBudgetResponse`).

## Behavior

Router, per distinct budget in first-appearance order:
- `get_project_budget_project_name` once. A 404 makes every item of that budget a failed result with that 404; other budgets continue.
- `_ensure_can_manage_project_budget_members` once. A 403 fails the whole request via the existing `_raise_access_denied`, before any write, across all distinct budgets.
- Items of found budgets, with original indexes, go to the service. One `session.commit()` at the end, then `budgets` is built with `get_project_budget` + `_build_project_budget_response`, once per budget with at least one accepted item, in first-appearance order. Budgets with no accepted item are not returned.

Service:
1. Phase 1, checks only (no writes, no LiteLLM, no ORM mutation). Load each budget, assignment and allocations once. Per item: budget exists, `_validate_member_amounts`, member exists (404 `Member allocation not found for user '...'`). Per budget: apply passing items to lightweight copies (later item wins for repeated users, member becomes FIXED), call `_equal_amounts_with_fixed_overrides` once. On failure every remaining item of that budget fails with 400 and its message; other budgets continue.
2. Phase 2: accepted items in input order, same writes and arguments as `override_member_allocation` (:1649-1677).
3. Phase 2b, pre-rebalance re-check on committed state. `get_active_by_budget_id` is a plain select on the same `AsyncSession` that loaded these rows in Phase 1, so SQLAlchemy returns the already-loaded objects without overwriting their attributes; a concurrent commit would be invisible. So:
   - `await session.flush()` first, so Phase 2 writes are in the DB and are not overwritten by the reload.
   - For every touched budget in first-appearance order: reload active allocations with `get_active_by_budget_id(..., populate_existing=True)`, and `await session.refresh(budget)` before reading `budget.max_budget` / `budget.soft_budget`.
   - Call `_equal_amounts_with_fixed_overrides(allocations, budget.max_budget, budget.soft_budget)`. On `ValidationException` raise `ExtendedHTTPException(code=400, message=str(exc))`, as `_resync_member_allocations` does. Not caught: the request fails with 400, and no rebalance or LiteLLM call has happened for any budget.
   - Reason: Phase 3 syncs to LiteLLM before the router commits, so a late failure for a later budget would leave earlier budgets' LiteLLM values ahead of a rolled-back DB. This only fires if a concurrent save changed fixed overrides or budget limits after Phase 1 (no lock).
4. Phase 3: unmodified `rebalance_project_budget` once per touched budget, sequentially. Its own re-check stays as is.
5. Phase 4: per accepted item, in input order, the `MEMBER_ALLOCATION_OVERRIDDEN` event and `member_allocation_override_completed` log exactly as :1681-1703.

If no item is accepted, the service skips Phases 2 to 4 entirely.

Request-level errors (401, 403, 422, Phase 2b 400, Phase 3 400, unexpected 500) roll back the whole batch. The session from `get_async_session` is closed without commit on exception (`clients/postgres.py:347`), so no explicit rollback code is needed.

## Acceptance criteria

1. Valid batch returns 200 with one result per item in input order and `budgets` for touched budgets in first-appearance order across all input items, including failed items. For input `[A/failed, B/ok, A/ok]`, return `budgets` in `[A, B]` order; budgets with no accepted item remain excluded.
2. A missing budget gives a failed 404 result for all its items; other budgets are processed.
3. Negative amounts are rejected by the request model with 422 (see 14). The only per-item 400 from `_validate_member_amounts` is `allocated_soft_budget > allocated_max_budget`, with today's message. A missing member gives a 404 item result with today's message. Other items still succeed.
4. The project-limit check runs once per budget on the final state; the ticket's order-dependent example succeeds for all three; if the final state exceeds the limit, all remaining items of that budget fail with 400.
5. Permission denied on any budget returns 403 for the whole request with no writes.
6. For a repeated (budget, user), the later accepted item wins; each accepted item gets its own result and audit event.
7. After Phase 2 writes are flushed, `_equal_amounts_with_fixed_overrides` runs once per touched budget on allocations and budget limits reloaded from the DB (not the session's cached objects). If it fails for any budget, including when stored amounts differ from the Phase 1 in-memory objects because of a concurrent commit, the request fails with 400 (message from the exception) and `rebalance_project_budget` and the provider are never called for any budget.
8. On the normal path, `rebalance_project_budget` runs exactly once per touched budget (none for budgets with no accepted item), unmodified.
9. One MEMBER_ALLOCATION_OVERRIDDEN event and one completion log line per accepted item; none for failed items.
10. When every item fails (per-item checks or the per-budget limit check), the response is 200 with all results failed, `budgets: []`, and no writes, no rebalance, no provider calls and no events.
11. Phase 1 performs no writes, no provider calls and no ORM row mutation.
12. A Phase 2b or Phase 3 400, or an unexpected error, leaves nothing committed in the DB.
13. The PATCH quirks are preserved: a null or omitted `override_reason` clears, and every accepted item sets the member FIXED.
14. 0 items, more than 1000 items, or negative amounts give 422.
15. The PATCH endpoint and `override_member_allocation` are unchanged; `get_active_by_budget_id` called without the new parameter behaves as before; the baselines are regenerated and list the new route ahead of `/{budget_id}`.

## Non-goals

- No UI changes and no "under 10 seconds" target.
- No locking between concurrent saves.
- No changes to rebalance_project_budget (including its own re-check), its callers, the LiteLLM adapter, ProjectMemberSyncExecutor, LITELLM_PROJECT_MEMBER_SYNC_WORKERS or the enterprise package.
- No other repository changes beyond the optional `populate_existing` parameter on `get_active_by_budget_id`; no change to existing callers.
- No compensation or rollback of LiteLLM values when a later rebalance fails.
- No parallel categories or merged provider calls; budgets stay sequential on one AsyncSession.
- No bulk clear or reset of overrides.
- No fixes to existing quirks and no other validation changes.
- No local-stack Verification or benchmark section, and no commit of local pyproject or compose overrides.
- No push and no MR without asking.

## Intended differences from the PATCH loop (to mention in the MR)

- Limit check once per budget on the final state.
- One `PROJECT_BUDGET_REBALANCED` event per budget.
- Permission checked once per budget; a denial fails the whole request with 403.
- One commit with whole-batch rollback.
- LiteLLM no longer sees intermediate rebalance values.
- An unexpected error during a later budget's rebalance can still leave earlier budgets' LiteLLM values ahead of the rolled-back DB. This is the existing provider-before-commit behavior, now spanning categories; with no lock it is accepted. The Phase 2b re-check removes the known limit-failure cause of this.

## Test plan outline

Service tests with patched repositories and provider (pattern of `test_project_budget_service_lifecycle.py`):
- Criteria 2-4, 6, 9, 11, 13.
- Criterion 10: all items failing yields all-failed results, no writes, rebalance, provider calls or events.
- (a) Phase 2b re-check failing for the second touched budget raises 400, and `rebalance_project_budget` and the provider are never called for any budget.
- (b) Normal path calls the re-check once per touched budget and `rebalance_project_budget` once per touched budget.
- (c) A reloaded row whose stored amounts differ from the Phase 1 in-memory object (simulating a concurrent commit) makes Phase 2b fail with 400 and no rebalance or provider call happens.

Repository test: `get_active_by_budget_id` called with defaults does not set `populate_existing`; with `populate_existing=True` it does.

Router tests (pattern of `test_project_budget_router.py`) covering 1, 5, 14, with the patched `get_project_budget_project_name` and permission helper. Router-guard baselines regenerated per `testing-api-patterns.md`.
