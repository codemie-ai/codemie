# EPMCDME-11032 Default budgets at user creation: Implementation Plan

**Goal:** On five user-creation paths, create the LiteLLM customer and the `user_budget_assignments` row for every configured default budget, fail-open, as soon as the account is usable. Spec: `spec.md` in this directory.

**Conventions:** Commit per task using the repository's existing convention. No code comments. Tests are mock-based, in the style of the neighbouring files. No docs pages. Whole-suite gates and manual verification belong to later stages.

**Fixed values (spec left them open).** Named module constants: the deadline in `service/budget/budget_service.py`, the concurrency in `service/budget/provision_limiter.py`:
- `PROVISION_DEADLINE_SECONDS = 30.0`: provisioning runs in the background, so the deadline bounds background work, not request latency. A cold create can take about 15 s on a degraded proxy, plus time queued for a slot; 30 s lets it finish instead of cutting it off.
- `PROVISION_PROVIDER_CONCURRENCY = 3`: one user's full set (platform, premium_models, optional cli) runs in parallel, while the shared default thread pool (`min(32, cpu+4)`, at least 5) keeps free threads.
- No p99 was measured (no proxy access here). The constants are the single tuning point.

## T1: Gate helper `is_budget_enforcement_active`

Test-first: yes: `is_budget_enforcement_active()` is False for check flag off and for a `noop` provider, True otherwise; the projects router calls the gate directly (no `_is_budget_provider_active` wrapper).

- Modify `src/codemie/service/budget/feature_gate.py`: add `is_budget_enforcement_active()` returning `bool(config.LLM_PROXY_BUDGET_CHECK_ENABLED) and get_active_provider().provider_name != "noop"` (import `get_active_provider` at function level if it creates a cycle).
- Modify `src/codemie/rest_api/routers/projects/crud.py`: remove `_is_budget_provider_active` and call the new helper at both of its call sites.
- Tests in `tests/codemie/service/budget/test_feature_gate.py`.

## T2: Provider contract `provision_category_customer` (was `provision_global_user`)

Test-first: yes: the LiteLLM adapter test fails until the new signature exists. It asserts the customer id is `build_user_id(username, category)`, the `budget_id` passes through, and the return is `resolve_mirrored_budget_id(customer, category)`. It also asserts `None` is returned, with nothing above debug logged, for a missing service, and `None` for a `None` customer. No provider exception type is added.

- New name and signature `provision_category_customer(*, user_id, username, budget_category: BudgetCategory, budget_id: str) -> str | None` in `service/budget/provider.py:259`, `provider_registry.py:167` (noop returns `None`) and `enterprise/litellm/budget_provider_adapter.py:1000`.
- Adapter: function-level imports of `build_user_id` (as in `assign_user_budget`, `:738`) and `resolve_mirrored_budget_id` from `llm_factory`. It calls `asyncio.to_thread(service.get_or_create_customer_with_budget, customer_id, budget_id)`. Keep debug logs; do not warm the customer cache.
- Update `tests/codemie/service/budget/test_provider_registry.py:70,118` for the new kwargs and `None` return. Add the adapter tests to `tests/enterprise/litellm/test_budget_provider_adapter.py`.

## T3: `BudgetService.provision_default_budgets(user_id, username)`

Test-first: yes: with a mocked provider and `track_proxy_budget_assignment_for_request`, the method calls the provider once per distinct configured category and then writes each row with the returned id. Further cases in `tests/codemie/service/budget/test_provision_default_budgets.py`:
- gate closed gives no provider call and no warning
- a provider `None` writes no row and logs one warning
- one category raising still writes the others
- a hanging provider (patch the deadline constant to ~0.05 s) returns at the deadline with a warning and no row
- a call that outlives the deadline keeps its semaphore slot until it finishes, and a raising call frees it
- a row-write failure in one category leaves the others written (for example an FK failure surfaced from the mirror call)

- Add the deadline constant to `budget_service.py`. Put the limiter in its own module, `service/budget/provision_limiter.py` (`budget_service.py` is far past the 500-line cohesion limit): `PROVISION_PROVIDER_CONCURRENCY`, `provision_semaphore()` returning an `asyncio.Semaphore` cached per running loop (`WeakKeyDictionary[AbstractEventLoop, Semaphore]`), because a semaphore binds to the loop it first runs on, and `call_provider_holding_slot(call)`. Limiter tests go in `tests/codemie/service/budget/test_provision_limiter.py`.
- Add `run_in_background(make_coro) -> bool` to `core/event_loop.py`: a task on the main loop when called on its thread, `run_coroutine_threadsafe` onto the main loop from any other thread, the current loop when no main loop runs, and `False` without creating the coroutine when no loop runs. It keeps each future in a module-level set until done. Tests in `tests/codemie/core/test_event_loop.py`.
- Add `schedule_default_budget_provisioning(user_id, username)` to `BudgetService`: it calls `run_in_background(lambda: self.provision_default_budgets(...))`, logs a debug line when nothing was scheduled, and logs a warning with `exc_info` instead of raising.
- Add to `BudgetService`, near `track_proxy_budget_assignment_for_request` (`budget_service.py:957`):

```python
async def provision_default_budgets(self, user_id: str, username: str) -> None
```

- Behavior: return quietly (debug log) unless `is_budget_management_enabled()` and `is_budget_enforcement_active()` (function-level import of `feature_gate`). Categories are the distinct `BudgetCategory(entry.budget_category)` over `budget_config.predefined_budgets`, with the budget id from `_default_budget_id_for_category`. Per category, under the semaphore, call `get_active_provider().provision_category_customer(...)`. On `None`, log a `budget_event` warning and write nothing. Otherwise call `self.track_proxy_budget_assignment_for_request(user_id, category, returned_id)`; it skips an existing row, uses its own session and sets the cache. `asyncio.gather(..., return_exceptions=True)` inside one `asyncio.wait_for(..., PROVISION_DEADLINE_SECONDS)`. Timeout or any exception becomes a `budget_event` warning (with `exc_info`, except the timeout) and the method never raises. No session parameter, no cache pop, no `enterprise` import.

## T4: Admin create hook (path 3)

Test-first: yes: `create_local_user_with_flow` calls `budget_service.schedule_default_budget_provisioning(new_user_id, username)` after `session.commit()` and passes the username, not the email. Scheduling and fail-open are tested on the helper itself.

- Modify `user_management_service.py:846-862`: replace the `asyncio.run(get_active_provider().provision_global_user(...))` block with `budget_service.schedule_default_budget_provisioning(new_user_id, username)`, with no `try/except` at the call site.
- Rewrite both tests in `tests/codemie/service/user/test_user_management_service_create_local_user.py` (they pin the old call), pinning the call and its order after the commit.

## T5: Registration paths 1 and 2

Test-first: yes: the registration test fails until the instant-login branch calls `schedule_default_budget_provisioning(user_id, username)` after `session.commit()` at `:246`. Cases:
- it is not called in the email-verification branch at `:209`
- `verify_email_and_login` calls `schedule_default_budget_provisioning` after the session closes, with the pre-commit `username`
- the router `verify_email` runs the service through `run_in_threadpool`

- `registration_service.py`: after the `:246` commit and the personal project step, call `budget_service.schedule_default_budget_provisioning` (it never raises, so no `try/except`). In `verify_email_and_login`, read `user.username` with `user_id` before the `:313` commit, then call it after the session closes.
- `local_auth_router.py:141`: `result = await run_in_threadpool(registration_service.verify_email_and_login, data.token)`.
- Tests in `tests/codemie/service/user/test_registration_service.py` (`:563` neighbourhood) and `tests/codemie/rest_api/routers/test_local_auth_router.py` (`:360-384`).

## T6: IdP first login and dev header (paths 4 and 5)

Test-first: yes: in `test_authentication_service.py`, patch `schedule_default_budget_provisioning` and fail unless:
- a freshly created IdP user triggers it once with `user_id` and `security_user_ins.username`
- the `IntegrityError` recovery branch does not trigger it
- an existing user does not trigger it
- dev-header new user triggers it once, after the commit
- dev-header existing user does not

- `authentication_service.py:489-517`: `_create_or_recover_user` returns `(db_user, pre_sync_email, created)`, with `True` only on the fresh-create return; update its single caller at `:555`. Keep `is_new_idp_user` and the `_sync_budget_for_idp_projects` trigger unchanged.
- `authenticate_persistent_user`: after `:571` commit, beside the project-sync hook (`:577`), `if created:` call `budget_service.schedule_default_budget_provisioning` directly (no wrapper, no `try/except`), using `user_id` and `security_user_ins.username`.
- `authenticate_dev_header`: `created = False` before the session block, set `True` in `if not db_user:`. After the `async with` closes (after the `:701` commit), `if created:` call it the same way, with `user_id` for both values.
- Tests in `tests/codemie/service/user/test_authentication_service.py` next to `:1122-1230` and `:1438`.

## Negative-constraint pass

- Customers or rows at the unverified commit `:209`: none; T5 hooks only `:246` and `:313`.
- Keycloak migration, `bootstrap_superadmin`: untouched; no task edits them.
- Lazy mirroring, startup backfill, `ensure_predefined_budgets`: untouched. T3 only calls the existing `track_proxy_budget_assignment_for_request`.
- Overwriting an existing row: avoided; no upsert call and no cache pop in T3.
- `cli` category unless configured: T3 iterates `predefined_budgets` only.
- Writing a row for a `None` provider result: T3 skips it and logs a warning.
- Core importing from `enterprise/litellm`: T2 keeps the imports inside the adapter; T3 has none.
- Schema or migration, cache warm-up, username validation, `_sync_budget_for_idp_projects` trigger change, new docs pages: no task includes them.
- Open-ended or ungated provisioning: T3 gates on both feature checks, and T4 now follows that gate.
- Arch-review plan notes: `created` flag in T6, one-category-failure test in T3. The warm-cache note is a spec non-goal and is not planned.
