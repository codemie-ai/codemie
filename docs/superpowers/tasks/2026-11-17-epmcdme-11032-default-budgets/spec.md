# EPMCDME-11032: Provision default budgets at user creation

## Problem

A new user gets a LiteLLM customer and `user_budget_assignments` rows only lazily on the first LLM request (`check_user_budget` creates the customer, `BudgetService.track_proxy_budget_assignment_for_request` writes the row). Until then the user has no visible default budget. Provision both for every configured default budget as soon as the account is usable.

## Behavior

One fail-open entry point on `BudgetService`, `schedule_default_budget_provisioning(user_id, username)`, called after the commit that makes the account usable, on five paths. It starts `provision_default_budgets(user_id, username)` in the background and returns at once, so no user-creation request waits on the provider:

1. Self-registration with instant login, `RegistrationService.register_user_with_flow`, after the commit at `registration_service.py:246`. The call never raises, so a failure cannot reach the outer `except Exception` that returns HTTP 500. The unverified-email commit at `:209` is not hooked.
2. Email verification, `RegistrationService.verify_email_and_login` (sync static), after the commit at `:313`, with `username` read before the commit like `user_id`. The route `verify_email` (`local_auth_router.py:127-141`) is `async def`, and this method does sync DB work, so the route calls it through `run_in_threadpool` to keep that work off the event loop.
3. Admin create, `UserManagementService.create_local_user_with_flow`, after `session.commit()` (`user_management_service.py:844`). It replaces the platform-only provider call at `:846-862` (then named `provision_global_user`).
4. IdP first login, `AuthenticationService.authenticate_persistent_user`, after `session.commit()` (`authentication_service.py:571`), next to `_sync_budget_for_idp_projects`. `_create_or_recover_user` returns an explicit `created` flag, and the hook runs only when it is true. The existing `is_new_idp_user` is also true on the `IntegrityError` recovery branch (`:555-558`, `:489`). Values come from `security_user_ins` or pre-commit locals, since `db_user` is detached.
5. Dev-header login, `AuthenticationService.authenticate_dev_header`, when a user was created in this call, after the single `await session.commit()` at `:701`. That commit is outside the `if not db_user:` block, so the hook is too.

Gate (else return silently, debug log at most): `is_budget_management_enabled()` and a new `is_budget_enforcement_active()` in `service/budget/feature_gate.py`, which holds today's router rule (`config.LLM_PROXY_BUDGET_CHECK_ENABLED` and active provider not `noop`, `crud.py:129-131`). The projects router (`crud.py`) calls it directly; its private `_is_budget_provider_active` wrapper is removed. A missing LiteLLM service is handled in the adapter, which returns `None` with a debug log. With the gate open this cannot happen in practice: the LiteLLM provider is registered at startup only when the service exists and the budget check is on (`rest_api/main.py:356`).

Provider contract: `provision_category_customer(*, user_id, username, budget_category, budget_id) -> str | None` (renamed from `provision_global_user`, which no longer described a per-category customer). The LiteLLM adapter builds the customer id with `build_user_id(username, category)`, creates the customer through `get_or_create_customer_with_budget` via `asyncio.to_thread`, and returns `resolve_mirrored_budget_id(customer, category)`. It returns `None` when the service is missing or the customer is `None`. The noop provider returns `None`. `BudgetService` imports nothing from `enterprise/litellm`.

For each distinct category in `budget_config.predefined_budgets`, run concurrently:

- Take a slot from a small per-loop semaphore bounding concurrent provider calls (the pool is the shared default thread pool). A slot is held until the provider call itself finishes, so a hung provider occupies at most that many threads.
- Call the provider. On `None`, skip with a warning and write no row. Lazy mirroring covers it later.
- Otherwise write the row with the existing `track_proxy_budget_assignment_for_request(user_id, category, budget_id)`: its own short session, opened only after the provider call returns, skips an existing row, sets the cache.

The whole gather is wrapped in one `asyncio.wait_for` deadline. On timeout the method logs a warning and returns, and lazy mirroring covers the rest. Because nothing waits on it, the deadline bounds background work, not request latency. The deadline and semaphore values are named constants in the plan. The method takes no session. A failure in one category does not affect the others or the user creation. Every failure is a `budget_event` warning and is never raised.

Background scheduling (`codemie.core.event_loop.run_in_background`):

- Paths 1, 4 and 5 run on the main event loop and get an `asyncio` task on it. Paths 2 and 3 run in sync worker threads and hand the coroutine to the main loop with `run_coroutine_threadsafe`, without waiting on the result.
- Every scheduled future is kept in a module-level set until it finishes, since the loop holds only weak references to tasks.
- With no running loop at all (scripts, startup), nothing is scheduled, the coroutine is never created, and a debug line is logged. Lazy mirroring and the startup backfill cover the user.

## Acceptance criteria

1. With the gate open and a running main loop, each of the five paths creates, for every distinct configured category, the LiteLLM customer `build_user_id(username, category)` and a `user_budget_assignments` row (`assigned_by="system"`, budget id from the provider's return), in the background shortly after the user is created. If the first LLM request comes first, lazy mirroring writes the row and the background run skips it.
2. With any gate condition closed (feature off, check disabled, noop provider, which covers LiteLLM being off or not installed), no customer is created and no row is written, and nothing above debug is logged.
3. A failure (provider exception, `None` return, DB error, deadline timeout, scheduling error) never fails or delays user creation on any path. A warning with the exception's stack trace is logged (the timeout warning has none) and lazy first-request mirroring still applies. A `None` return writes no row.
4. When one category fails (for example its budget id is missing from the local `budgets` table), the other categories still get their customers and rows.
5. Rows are written by separate short sessions, none shared across categories and none open during the provider call.
6. Provider calls are bounded by the semaphore, and a provider call that never returns ends the method at the deadline, with a test using a hanging provider.
7. A re-run for a user who already has a row for a category leaves that row (budget id and `assigned_by`) unchanged, and creates no duplicate customer.
8. Customer ids use the username and not the email or user id, on all five paths.
9. Registration with email verification on creates nothing at `:209`; it creates customers and rows after the verification commit (path 2) or the instant-login commit (path 1).
10. On IdP first login, the `IntegrityError` recovery branch triggers no provisioning, and a freshly created user does. Dev-header provisioning runs only when the user was created in that call.
11. No user-creation request waits on provisioning, on any path, and nothing blocks the event loop. `verify_email` calls the service through `run_in_threadpool`.
12. `provision_category_customer` has the new signature and return in the protocol, the noop and the LiteLLM adapter. The projects router uses the new gate directly. Tests pinning the old signature (`test_provider_registry.py`, `test_user_management_service_create_local_user.py`) and `test_local_auth_router.py` are updated. New mock-based tests cover the gate, each path's hook, the `created` flag, fail-open, the `None` skip, the deadline, the limiter, and background scheduling from async code, from a worker thread and with no loop, in the style of the neighbouring tests.

## Non-goals

- Keycloak bulk migration (`enterprise/migration/coordinator.py`) is untouched and keeps lazy mirroring plus startup backfill.
- SuperAdmin bootstrap (`UserManagementService.bootstrap_superadmin`) is not hooked.
- No provisioning at the unverified-email registration commit (`:209`).
- No schema change or migration.
- No change to lazy first-request mirroring, startup `backfill_user_budget_assignments`, or `ensure_predefined_budgets`.
- No `cli` category unless configured in `predefined_budgets`.
- No backfill of existing users, and no cross-replica cache invalidation (the cache is per process, TTL 60s).
- The trigger condition of the existing `_sync_budget_for_idp_projects` hook is unchanged.
- No warm-up of the LiteLLM customer cache after the eager create.
- No username validation or reserved-username rules.
- No new feature documentation pages.

## Decisions

- Extend `provision_global_user`, renamed `provision_category_customer` (category and budget id in, mirrored budget id or `None` out), over adding a second provider method: one contract for eager provisioning, and its sole production caller is replaced. — user, answers AR-3
- The provider builds the customer id and resolves the mirrored budget id, and returns `str | None`, over returning the LiteLLM customer to `BudgetService`: the core service must not import enterprise helpers. — user, answers AR-3
- One `is_budget_enforcement_active()` in `feature_gate.py`, with the projects router calling it directly, over a copy of the rule in the budget service or an import of the router's private function. — user, answers AR-3
- Rows are written through `track_proxy_budget_assignment_for_request` over a new upsert writer: one writer, own short session, skips an existing row. — user, answers AR-1
- The existing row is preserved over overwritten by the upsert: matches the lazy mirror and backfill, and a re-run cannot clobber an admin-set budget. — user, answers AR-1
- Each category writes in its own session after the provider call over one shared session across concurrent categories. — user, answers AR-1
- One overall `asyncio.wait_for` deadline plus a semaphore over an unbounded gather, with lazy mirroring as the fallback on timeout. — user, answers AR-2
- A `None` provider result skips the row and logs a warning over writing a row with the configured default budget id: that would record a budget the customer does not have, and nothing later corrects it.
- All paths schedule through one `run_in_background` helper in `core/event_loop.py` over a sync bridge such as `run_coroutine_blocking`: nothing waits, so no thread or event loop can be blocked by a slow provider. — user, post-review (replaces the AR-4 answer)
- `schedule_default_budget_provisioning` and `provision_default_budgets` never raise, so call sites add no `try/except` of their own. — user, post-review
- A missing LiteLLM service returns `None` from the adapter over a dedicated provider exception: with the gate open it is only reachable in an inconsistent state, where a warning is right. — user, post-review
- No provisioning at the unverified registration commit; provisioning when the account becomes usable (instant-login commit and email-verification commit) over provisioning at registration. — user, answers AR-5
- Path 2 (email verification) is hooked inside `verify_email_and_login`, and the route runs it through `run_in_threadpool` over keeping the direct call: it keeps the method's sync DB work off the event loop. — user, answers verify-hook question
- Out of scope: username collisions with another user's category customer id. — user, answers AR-6
- Provisioning runs as a background task over being awaited inline: inline waiting added up to the deadline to every user creation during a LiteLLM outage. Budgets appear shortly after creation, not before the response. — user, post-review (replaces the inline decision)
- Path 4 detects a fresh create through an explicit `created` flag from `_create_or_recover_user` over the `is_new_idp_user` flag or the tuple's second element: the flag is also set on the recovery branch.
- Path 5's hook runs after the commit at `authentication_service.py:701`, outside the `if not db_user:` block, over inside it: the create is only flushed in the block.
- The previously ungated admin-create call now follows the same gate as the other paths over staying ungated.

## Risks and assumptions

- Time to visible budgets: each category makes 1 to 3 blocking LiteLLM calls, each bounded at 5 s by the client timeout (`codemie_enterprise/litellm/config.py:24`), so a category can take about 15 s, and a burst of new users queues on the semaphore and the shared thread pool. The deadline and semaphore size are unmeasured and are fixed in the plan.
- Background work in flight when a worker shuts down is lost. Lazy mirroring and the startup backfill cover it.
- If the first LLM request races the background run, both may try to create the same LiteLLM customer; the second create fails and is logged, and the DB side skips the existing row.
- A row written with a wrong budget id persists, since lazy mirroring and backfill skip existing rows. The `None` skip and the customer's own attached budget id limit this.
- Whether the active provider is `noop` whenever the check flag is off is not verified. The gate checks both.
- The history claim about `c087d7bed9` and the Jira body are unverified. They do not change the design.
