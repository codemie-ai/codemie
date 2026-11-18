# Technical Research

**Task**: budget litellm user-creation provisioning
**Generated**: 2026-11-17
**Research path**: codegraph

---

## 1. Original Context

Review https://jiraeu.epam.com/browse/EPMCDME-11032 and implement fix. Initial investigation and proposed solution available at a local plan document (not committed, not linked). Proceed dirty.

Requirements as supplied (the Jira ticket itself is not readable from here; the plan text is treated as the ticket body):

- **Problem**: a new user gets no default budget when created. The LiteLLM customer and the `user_budget_assignments` rows appear only on the first LLM request: `check_user_budget` creates the customer, `track_proxy_budget_assignment_for_request` writes the row, both called from `enterprise/litellm/llm_factory.py` and `enterprise/litellm/proxy_router.py`.
- **Goal**: create the LiteLLM customer and the assignment row for every configured default budget as soon as user creation is committed.
- **Gate** (otherwise do nothing, log nothing above debug): `is_budget_management_enabled()`; `get_litellm_service_or_none()` not None; `config.LLM_PROXY_BUDGET_CHECK_ENABLED` and `get_active_provider().provider_name != "noop"` (same pair as `_is_budget_provider_active` in `rest_api/routers/projects/crud.py`).
- **Paths in scope**: (1) `RegistrationService.register_user_with_flow`, after commit; (2) `UserManagementService.create_local_user_with_flow`, after commit, replacing the platform-only `provision_global_user` call; (3) `AuthenticationService.authenticate_persistent_user`, after `session.commit()`, only when `_create_first_login_user` actually created the user (not the `IntegrityError` recovery branch); (4) `AuthenticationService.authenticate_dev_header`, inside `if not db_user:`, after the create is committed.
- **Out of scope** (keep lazy first-request mirroring): Keycloak bulk migration (`enterprise/migration/coordinator.py`); SuperAdmin bootstrap (`UserManagementService.bootstrap_superadmin`).
- **Proposed solution**: one fail-open method, e.g. `BudgetService.provision_default_budgets(session, user_id, username)`. For each `budget_config.predefined_budgets` entry: customer id `build_user_id(username, BudgetCategory(entry.budget_category))`; create the customer through the budget provider (extend `provision_global_user` in `service/budget/provider.py`, the LiteLLM adapter and the noop provider to take category and budget id and return the customer, via `asyncio.to_thread(service.get_or_create_customer_with_budget(customer_id, budget_id))`); write the row with `budget_repository.upsert_user_category_assignment(..., assigned_by="system")` using the budget id from `resolve_mirrored_budget_id(customer, category)`; commit, then drop `(user_id, category)` from `_budget_assignment_cache`. Run categories concurrently (`asyncio.gather`).
- **Notes**: loop over configured categories only (shipped yaml has `platform`/`default` and `premium_models`/`default_premium_models`; `cli` off unless configured); idempotent; customer identity is the username, not email or user id; between 2026-04-24 and 2026-05-08 SSO first login created customers keyed by user UUID and commit `c087d7bed9` removed that call; predefined budget rows normally exist because `ensure_predefined_budgets` runs at startup, a missing row only causes a logged fail-open skip.

---

## 2. Codebase Findings

### Existing Implementations

**Purpose A: a user has a LiteLLM customer and a `user_budget_assignments` row per default budget before their first LLM request**

- Lazy customer creation on first request: `check_user_budget(username, budget_id, user_id)` `src/codemie/enterprise/litellm/dependencies.py:440`. Cache-first (`_get_cached_customer`), else `get_or_create_customer_with_budget(username[, budget_id])` (:499-501). Fail-open: returns None when service missing or customer empty. 19 callers incl. `llm_factory.py`, `proxy_router.py`.
- Lazy row write: `BudgetService.track_proxy_budget_assignment_for_request` `service/budget/budget_service.py:957`. Skips on empty budget_id, on `_budget_assignment_cache` hit equal to budget_id, and when a row already exists (existing row is cached, not overwritten, :990-998); otherwise upserts with `assigned_by` default "system". Callers: `llm_factory.py:538` (`_mirror_budget_assignment`, via `run_coroutine_threadsafe` on the main loop), `proxy_router.py:1234`.
- Budget id chosen for the row: `resolve_mirrored_budget_id(customer, category)` `llm_factory.py:491`. Takes the customer's attached `litellm_budget_table.budget_id`; falls back to `get_category_budget_id(category)` (configured default) when the customer or its table is None. With `customer=None` it still returns the configured default. Callers: `llm_factory.py:531`, `proxy_router.py` (3 callers total).
- Customer ids per category: `build_user_id` `enterprise/litellm/budget_categories.py:26` (platform: bare username; others `{username}_codemie_{category.value}`). First-request derivations: `get_proxy_username` `dependencies.py:619` (cli, None unless a cli budget is configured), `get_premium_username` :604 (None unless the model is premium). Config presence checks: `get_category_budget_id` :553, `is_premium_models_enabled` :566 and `is_proxy_budget_enabled` :579 (both `lru_cache`d, process lifetime).
- Customer creation semantics (sibling repo `/Users/bohdan_maliar/Projects/codemie-dev/codemie-enterprise/src/codemie_enterprise/litellm/service.py:1237`): sync; `get_customer_info`, then `get_or_create_budget` + `create_customer` when absent; budget id defaults to `self.config.default_budget_id` when omitted; an existing customer with any attached budget is returned unchanged; with no attached budget it deletes and recreates; exceptions are logged and swallowed, returning None (or the old customer). It does not raise on failure.
- Existing eager provisioning, platform only: `ProviderProtocol.provision_global_user(*, user_id, username) -> None` `service/budget/provider.py:259`; LiteLLM impl `enterprise/litellm/budget_provider_adapter.py:1000` (gets service, returns silently if None, `asyncio.to_thread(service.get_or_create_customer_with_budget, username)` with no budget id, no category, returns nothing, writes no row); noop impl `service/budget/provider_registry.py:167`. Single production caller: `UserManagementService.create_local_user_with_flow` `service/user/user_management_service.py:846-862` (after `session.commit()`; `asyncio.run(...)` inside try/except logging a warning; no feature/enforcement gate, calls the active provider whatever it is). Distance from purpose: covers path 2 and platform category only, no row.
- Sibling provider calls that already take category + budget id: `assign_user_budget(username, budget_category, budget_id)` adapter `budget_provider_adapter.py:730` (builds `build_user_id(username, budget_category)`, calls `update_customer_budget_in_litellm` in `budget_helpers.py:196` which assigns via `set_customer_budget_assignment`, never raises), `reset_user_budget_spending` (`budget_service.py:1399`, recreates customers per category, fail-open warning per category).
- Existing eager row write at a non-request moment: `BudgetService._record_default_assignment` `budget_service.py:1386` (`upsert_user_category_assignment(..., assigned_by="system")` then `_budget_assignment_cache.pop((user_id, category.value), None)`); other upsert sites `budget_service.py:873` (backfill), :1190, :1325. Repo method `budget_repository.upsert_user_category_assignment` `repository/budget_repository.py:319` (INSERT ON CONFLICT (user_id, category) DO UPDATE budget_id/assigned_by/assigned_at; flushes, does not commit).
- Existing bulk mirroring of customers to rows: `BudgetService.backfill_user_budget_assignments` `budget_service.py:907`, run once per startup by `BudgetStartupReconciliationService._run_steps` `service/budget/startup_reconciliation_service.py:64` (step after `ensure_predefined_budgets` and `sync_budgets_from_litellm`), gated by `config.LLM_PROXY_BUDGET_RECONCILIATION_ENABLED`; skips existing rows, imports provider customers with a budget, `missing_user` when no matching user.
- Predefined budgets: `budget_config.predefined_budgets` (`configs/budget_config.py:38`, entries `PredefinedBudgetConfig`: budget_id, name, soft/max, duration, `budget_category`), shipped file `config/budgets/budgets-config.yaml` (`default`/platform, `default_premium_models`/premium_models). `BudgetService._default_budget_id_for_category` :572 reads it; `ensure_predefined_budgets` :681 (startup, `rest_api/main.py:453` wrapper, `enterprise/litellm/dependencies.py:242`).

**Purpose B: only provision when budgeting is on**

- `is_budget_management_enabled()` `service/budget/feature_gate.py:31` (`customer_config.is_feature_enabled("budgetManagement")`).
- `get_litellm_service_or_none()` `dependencies.py:165` (None when enterprise package missing, `LLM_PROXY_ENABLED` off or service uninitialised, per its docstring :171-175).
- `_is_budget_provider_active()` `rest_api/routers/projects/crud.py:129` (private to the router module; `bool(config.LLM_PROXY_BUDGET_CHECK_ENABLED) and provider.provider_name != "noop"`); also used `crud.py:136, 310`. `config.LLM_PROXY_BUDGET_CHECK_ENABLED` defaults False (`configs/config.py:786`). User-router gate pattern: `require_litellm_enabled()` + `ensure_budget_management_enabled()` (`user_management_router.py:509-510`).

**Purpose C: user creation points**

- Registration: `RegistrationService.register_user_with_flow` `service/user/registration_service.py:154`; sync `get_session()` inside an async method; two commit sites: `session.commit()` :209 (email verification on, returns "message") and :246 (instant login, then builds token). Whole body wrapped by `except Exception` -> HTTP 500 "Registration failed" (:283). Personal project is created after each commit via `personal_project_service.ensure_personal_project_async` (:215, :252). `user_id`, `user_email`, `user_type` are extracted before commit; `username` is a parameter. 6 callers in `local_auth_router.py`.
- Admin create: `create_local_user_with_flow` `user_management_service.py:793` (sync; route `create_user` is a sync `def` at `user_management_router.py:211`, so `asyncio.run` works in the threadpool). `username` is a parameter.
- IdP first login: `AuthenticationService.authenticate_persistent_user` `service/user/authentication_service.py:520`. Async session; `is_new_idp_user = True` is set whenever the `else` branch ran (:555-558), including the `IntegrityError` recovery path of `_create_or_recover_user` :489 (which returns `(db_user, pre_sync_email)` with a non-None email after `_sync_existing_user`; the fresh-create path returns `(db_user, None)`). `session.commit()` :571. Post-commit hook already present for new IDP users: `_sync_budget_for_idp_projects(user_id, project_names)` :577-581 (fail-open warning). `db_user` is detached after the session closes (comment :574-576); `security_user_ins` (built from `db_user` via `_build_security_user` before commit) carries username/id/email. Result cached by `auth_token` in `_auth_token_cache` (:539, :593), so later requests skip this method. 9 callers in `rest_api/security/user_providers/persistent.py`.
- Dev header: `authenticate_dev_header` :638; creates `UserDB(id=user_id, email=user_id, username=user_id, ...)` via `user_repository.acreate` (flush only), single `await session.commit()` :701 after the login activity event; so the post-commit point is after :701, not inside the `if not db_user:` block. 3 callers in `persistent.py`.
- Not hooked today: `bootstrap_superadmin` `user_management_service.py:619` (calls `create_local_user` directly, username "admin"; commit in `bootstrap_superadmin_startup` :676); `enterprise/migration/coordinator.py` has no budget/litellm references.

**Other touched code**: `service/budget/budget_service.py` (`BudgetService`, singleton `budget_service`; imports `budget_config`, `get_active_provider`, core `BudgetCategory`); `service/budget/provider.py` and `provider_registry.py` (protocol and noop); `enterprise/litellm/budget_provider_adapter.py` (LiteLLM impl). `Budget`/`UserBudgetAssignment` models `service/budget/budget_models.py:128, :247`.

### Decisions Already in the Code

- Fail-open for budget side effects around user creation: warning log, never block (`user_management_service.py:859-862`, `authentication_service.py:577-581`, `reset_user_budget_spending` per category). Recorded reason in message: "will retry on first LLM request".
- Customer identity is the username, personal projects are email-based (`.ai-run/guides/integration/budget-attribution.md`, "Identity and analytics", and Avoid/Prefer row). Existing test pins it: `tests/codemie/service/user/test_user_management_service_create_local_user.py:63-91` (username kwarg, no email).
- Post-commit hooks for user creation use fresh/isolated sessions (`ensure_personal_project_async`, `_sync_budget_for_idp_projects` opens its own `get_session`) rather than the creating session.
- Two `BudgetCategory` enums with identical values: core `service/budget/budget_enums.py:20` (used by provider protocol, adapter, `BudgetService`, repository) and enterprise `enterprise/litellm/budget_categories.py:20` (used by `build_user_id`, `get_category_budget_id`). The adapter passes the core enum straight into `build_user_id` (`budget_provider_adapter.py:741`); `resolve_mirrored_budget_id` converts by `.value`. Provider-agnostic core must not branch on `provider_metadata` (`budget_models.py` docstring).
- Row is only mirrored when absent (first-request path keeps an existing row, :990-998); `upsert_user_category_assignment` overwrites.
- `_budget_assignment_cache` is a process-local `TTLCache` (TTL `BUDGET_ASSIGNMENT_CACHE_TTL`=60s, `config.py:382`), so a `pop` clears only the replica that wrote; `_record_default_assignment`'s docstring records that proxy replicas keep private caches.
- Customer-creation failure is signalled by a None return, not an exception (enterprise `service.py:1266-1268`); `resolve_mirrored_budget_id(None, category)` still yields the configured default id.
- Data direction: the first-request mirror keys on category from the request path; the eager path would iterate configured predefined budgets (stored as a list with string `budget_category`, validated against the enum elsewhere in `_validate_constraints` :175).

### Task Statements Checked Against the Code

- "`check_user_budget` creates the customer, `track_proxy_budget_assignment_for_request` writes the row, both called from `llm_factory.py` and `proxy_router.py`" - holds (`dependencies.py:440-501`, `budget_service.py:957-1004`, `llm_factory.py:531-544`, `proxy_router.py:1234`).
- "Gate: `is_budget_management_enabled()`; `get_litellm_service_or_none()` covers HAS_LITELLM, LLM_PROXY_ENABLED and initialised service; same pair as `_is_budget_provider_active`" - holds (`feature_gate.py:31`, `dependencies.py:165-202`, `crud.py:129-131`). `_is_budget_provider_active` is module-private in a router.
- Path 1 "after commit, async method, sync session" - holds, with two commit sites (:209, :246) and an outer `except Exception` that turns any raise into HTTP 500.
- Path 2 "replaces the existing platform-only `provision_global_user` call; sync, `asyncio.run` as today" - holds (:846-862). Keeps in place the existing mechanism under Purpose A (provider method `provision_global_user`, its adapter and noop implementations, and the tests below).
- Path 3 "only when `_create_first_login_user` actually created the user, not the IntegrityError branch" - the existing `is_new_idp_user` flag does not make that distinction (set at :558 in both cases); contradicted for the existing flag, the distinction is observable only through the second tuple element (None on create) or by restructuring `_create_or_recover_user`.
- Path 4 "inside `if not db_user:`, after the create is committed" - partly contradicted: the create is flushed at `acreate` and the commit happens once at :701 after the `if` block, so "after commit" is outside that block.
- "SuperAdmin bootstrap calls `create_local_user` directly, so the hook in #2 does not reach it" - holds (`user_management_service.py:639`, hook lives in `create_local_user_with_flow` :830-862). "Runs at startup before feature flags are loaded, before predefined budget rows exist, inside a running event loop" - unverified (startup call site not read; `bootstrap_superadmin_startup` is sync).
- "Keycloak bulk migration out of scope" - consistent: `coordinator.py` has no budget code; it keeps lazy mirroring plus startup `backfill_user_budget_assignments` (see Existing Implementations) in place.
- "`get_or_create_customer_with_budget(customer_id, budget_id)` keeps a budget already attached to an existing customer" - holds (enterprise `service.py:1270-1282`). "Each category costs one to three blocking LiteLLM calls (`get_customer_info`, `get_or_create_budget`, `create_customer`)" - holds (:1251-1263; recreation path adds `delete_customers`).
- "`get_or_create_customer_with_budget` and `upsert_user_category_assignment` are idempotent; first-request path skips the write when a row exists" - holds (:1270-1273; `budget_repository.py:327`; `budget_service.py:990`).
- "Drop `(user_id, category)` from `_budget_assignment_cache` as `_record_default_assignment` does" - holds (`budget_service.py:1397`); cache is per process.
- "Shipped yaml defines platform and premium_models; cli off unless configured; code treats a missing category as disabled" - holds (`budgets-config.yaml`, `dependencies.py:566-583`).
- "`ensure_predefined_budgets` runs at startup" - holds (`startup_reconciliation_service.py:68`), but it is the first step of a reconciliation gated by `LLM_PROXY_BUDGET_RECONCILIATION_ENABLED` and an advisory lock (:92-99), not an unconditional startup call.
- "Between 2026-04-24 and 2026-05-08 SSO first login created customers keyed by user UUID; `c087d7bed9` removed that call" - unverified (no git access from here; commit hash occurs only in the plan; what would settle it: `git show c087d7bed9`).
- Jira ticket body - unverified (auth required).

### Consumers of Changed Contracts

Contract 1: `provision_global_user(*, user_id, username) -> None` (signature extended with category/budget id and a return value).
- Protocol `service/budget/provider.py:259`; noop `provider_registry.py:167`; LiteLLM adapter `budget_provider_adapter.py:1000`; sole caller `user_management_service.py:853`.
- Tests: `tests/codemie/service/budget/test_provider_registry.py:70` and `:118` (call noop with `user_id`, `username` only), `tests/codemie/service/user/test_user_management_service_create_local_user.py:29-91` (patches `get_active_provider`, asserts `provision_global_user` awaited once with `username="alice_corp"` and no `user_email`, and `logger.warning` called once on failure). Any signature change or call-site replacement affects these.
- No other implementors found (searched `provision_global_user` across `src` and `tests`). `tests/enterprise/litellm/test_budget_provider_adapter.py` has no provision test.

Contract 2: `user_budget_assignments` rows created at user creation instead of first request. Readers: `budget_repository.get_user_category_budget_id` / `get_user_category_assignments` (:384-399), `GET /{user_id}/budgets` (`user_management_router.py:521`), `BudgetService.get_user_category_budget_id_for_request` and the batch lookups with negative caching (`budget_service.py:1019-1122`), `count_assignments` (`budget_repository.py:195`, used by `_check_category_change_allowed` :543, which returns 409 on category change while assignments exist), unassign flow `_record_default_assignment` :1386, `reset_user_budget_spending` :1399, `bulk_set_user_budgets` (`PUT /budgets/bulk`), `backfill_user_budget_assignments` :907 (skips users with a row), `user_repository.py` and `service/budget/__init__.py` (reference `UserBudgetAssignment`, not read in detail). The first-request path skips the write when a row exists, so the eager row is what it will then read.

Contract 3: `register_user_with_flow`, `create_local_user_with_flow`, `authenticate_persistent_user`, `authenticate_dev_header` gain a post-commit step. Tests: `tests/codemie/service/user/test_registration_service.py` (10 refs), `tests/codemie/rest_api/routers/test_local_auth_router.py` (3), `tests/codemie/service/user/test_authentication_service.py` (first-login tests at :1122-1230 patch `get_async_session` and `_sync_budget_for_idp_projects` and use `AsyncMock` sessions; `test_authenticate_dev_header_new_user` :1438), `tests/codemie/service/user/test_user_management_service.py` (budget_sync mocks :314-565, order assertion `["budget_sync","budget_sync","commit"]` :523 for project assignment, not user creation).

### Architecture and Layers Affected

Router (`user_management_router`, `local_auth_router`, persistent user provider) -> user services (`RegistrationService`, `UserManagementService`, `AuthenticationService`) -> budget service (`BudgetService`) -> provider port (`BudgetEnforcementProvider` protocol; noop + LiteLLM adapter in `enterprise/litellm`) -> repository (`budget_repository`) / external `codemie_enterprise.litellm.LiteLLMService`. Config layer: `budget_config`, `config`.

### Integration Points

LiteLLM proxy over HTTP through the enterprise `LiteLLMService` (sync client; adapter uses `asyncio.to_thread`); Postgres via async sessions (`get_async_session`, `postgres.py:415`) for budget repository and sync sessions (`get_session`) in registration/admin paths; `customer_config` feature flags; `codemie.core.event_loop._main_event_loop` for the sync-to-async mirror in `llm_factory`.

### Patterns and Conventions

- Provider-agnostic core calls the active provider (`get_active_provider()`), LiteLLM specifics stay in `enterprise/litellm`.
- `budget_event=<name> component=<c> ...` structured log lines (debug for skips, warning for failures).
- `BudgetService` is a singleton (`budget_service`) with async methods taking an `AsyncSession`; repositories flush, callers commit.
- Fail-open post-commit hooks with `logger.warning(... exc_info=True)`.
- No code comments added (user preference); tests are mock-based like neighbours.

---

## 3. Documentation Findings

### Guides and Architecture Docs

- `.ai-run/guides/integration/budget-attribution.md`: customer id is the username (not email, not UUID); "Keying a personal LiteLLM customer on the email" is listed as Avoid; says `budget_service` provisions personal budgets on `db_user.username`.
- Other guides listed in AGENTS.md (layered architecture, service-layer patterns, error handling, logging, configuration, testing) were not individually read.

### Architectural Decisions

None recorded for eager vs lazy customer creation. The comment/log text in `create_local_user_with_flow` ("will retry on first LLM request") and the `provision_global_user` docstring ("Called at new-user creation time (SSO first login, admin create)") state the intended behaviour; only admin create calls it today.

### Derived Conventions

See Patterns and Conventions above.

---

## 4. Testing Landscape

### Existing Coverage

- `tests/codemie/service/user/test_user_management_service_create_local_user.py` - hook in `create_local_user_with_flow` (warning on failure, username not email).
- `tests/codemie/service/user/test_authentication_service.py` - first IDP login with `_sync_budget_for_idp_projects` (:1122, :1194), dev header new user (:1438).
- `tests/codemie/service/user/test_registration_service.py`, `tests/codemie/rest_api/routers/test_local_auth_router.py` - registration flow.
- `tests/codemie/service/budget/test_budget_service.py` (upsert patches :730, :783; cache tests :804-884), `test_unassign_default_assignment.py` (`_record_default_assignment`, stale proxy cache), `test_provider_registry.py` (noop provider), `tests/enterprise/litellm/test_litellm_dependencies.py` and `tests/codemie/service/llm_service/test_litellm_service.py` (reference `get_or_create_customer_with_budget`), `tests/enterprise/litellm/test_proxy_router.py` (`track_proxy_budget_assignment_for_request` patched), `tests/codemie/configs/test_budget_config.py`.

### Testing Framework and Patterns

pytest with `pytest.mark.asyncio`, `unittest.mock` (`patch`, `AsyncMock`, `MagicMock`, `SimpleNamespace` providers); sessions mocked via `_make_async_session_cm` or `get_session.return_value.__enter__`; `clear_budget_assignment_cache()` used for cache isolation. Repo guide: mock-based, no real-Postgres tests in the user/budget service areas.

### Coverage Gaps

- No test exercises the LiteLLM adapter's `provision_global_user` (`budget_provider_adapter.py:1000`).
- No tests for gating (budget management / LiteLLM / enforcement) around user creation; no tests for registration hooks on budgets; no tests for IDP first-login recovery-branch distinction.

---

## 5. Configuration and Environment

### Environment Variables

- `LLM_PROXY_BUDGET_CHECK_ENABLED` (default False, `config.py:786`), `LLM_PROXY_ENABLED`, `LLM_PROXY_BUDGET_RECONCILIATION_ENABLED` (startup reconciliation), `BUDGET_ASSIGNMENT_CACHE_TTL` (60) and `BUDGET_ASSIGNMENT_CACHE_MAX_SIZE` (50000) (`config.py:382-383`), `ENABLE_USER_MANAGEMENT`, `IDP_PROVIDER`, `EMAIL_VERIFICATION_ENABLED`.

### Configuration Files

- `config/budgets/budgets-config.yaml` (via `config.BUDGETS_CONFIG_DIR`, `config.py:142`): predefined budgets, `BudgetConfig` `configs/budget_config.py:38`.
- Customer feature flag `features:budgetManagement` via `customer_config`.

### Feature Flags and Deployment Concerns

Enterprise package optional (`HAS_LITELLM`); enterprise `LiteLLMService.config.default_budget_id` is separate from `budget_config`. Enterprise code lives in sibling repo `/Users/bohdan_maliar/Projects/codemie-dev/codemie-enterprise`; per user memory, `--reload` ignores enterprise edits.

---

## 6. Risk Indicators

- `provision_global_user` is a shared protocol with three implementations and four tests; the existing call in `create_local_user_with_flow` is ungated (runs against the noop provider too) and its tests pin the current kwargs (`test_user_management_service_create_local_user.py`, `test_provider_registry.py`). See Consumers, Contract 1.
- Existing mechanism for the same purpose that the plan keeps in place: lazy mirroring (`check_user_budget` + `track_proxy_budget_assignment_for_request`) and startup `backfill_user_budget_assignments`; both skip when a row exists, so an eager row written with a wrong budget id will persist. See Existing Implementations, Purpose A.
- Customer creation signals failure via a None return, and `resolve_mirrored_budget_id(None, ...)` returns the configured default: Speculative: writing a row after a failed customer create would record a budget the customer does not have.
- Path 3: `is_new_idp_user` is set on the recovery branch as well; the created/recovered distinction is only the tuple's second element. Path 4: commit occurs outside the `if not db_user:` block. See Task Statements.
- Registration path wraps everything in `except Exception` -> HTTP 500; Speculative: an unguarded raise from a new step would fail a committed registration.
- Latency: first-login hook sits on the request that also builds the user; each category costs 1-3 sync LiteLLM calls, no timeout seen in the adapter path (`asyncio.to_thread`).
- `_budget_assignment_cache` is per process; other replicas may hold stale negative/positive entries up to 60s.
- Enterprise service lives in another repo and is not covered by core tests; `tests/.../test_budget_provider_adapter.py` has no coverage of `provision_global_user`.
- Two `BudgetCategory` enums with the same values; mixing them at the provider boundary is existing practice (`budget_provider_adapter.py:741`).
- `username` for dev-header users is the user id string; for IdP users, `db_user` is detached after commit so the hook must take values from `security_user_ins` or pre-commit locals.
- Commit `c087d7bed9` and the Jira ticket could not be inspected; history claim unverified.

---

## 7. Summary for Complexity Assessment

The change spans the user-service layer (registration, admin create, IdP first login, dev-header login: four call sites in three services), the budget service, the provider port (protocol, noop and LiteLLM adapter implementations) and the budget repository. Core pieces already exist and are reusable by name: `build_user_id`, `resolve_mirrored_budget_id`, `upsert_user_category_assignment`, `_record_default_assignment`'s write-and-cache-pop pattern, `budget_config.predefined_budgets`, and an existing post-commit fail-open hook on the IdP path. The surface is roughly 6-8 source files plus their tests; no schema or migration is involved (rows use the existing `user_budget_assignments` table and upsert).

Technical novelty is low to moderate: the only non-trivial points are the sync/async seams (registration is async with a sync session, admin create uses `asyncio.run`, IdP and dev header are async), the provider-protocol signature change that touches an existing production caller and two test files, distinguishing a fresh IdP create from the race-recovery branch, and the dev-header commit placement. Customer-creation failure is signalled by a None return rather than an exception.

Test posture: user-creation hooks and the adapter's `provision_global_user` have thin or no coverage; neighbours use mock-based pytest with `AsyncMock` sessions, so new tests follow an established style. Key risks are the shared provider contract, per-process assignment cache, login-path latency, and eager rows that the lazy and startup-backfill paths will not correct.

---

## 8. External References

None named by the task as a path or URL for reading beyond the Jira link (https://jiraeu.epam.com/browse/EPMCDME-11032), which requires authentication and was not readable; the requirements were taken from the supplied plan text. Sibling repository file consulted for behaviour of `get_or_create_customer_with_budget`: `/Users/bohdan_maliar/Projects/codemie-dev/codemie-enterprise/src/codemie_enterprise/litellm/service.py:1237-1301` (resolved; facts under Existing Implementations).

## Facts Added by Review

Researched for the names the spec relies on, after the analysis above was written. Read like the facts layer; where a line here contradicts one above, this one was read later.

- `ProjectAssignmentService._run_budget_provider_coro(coro)` - `src/codemie/service/project/project_assignment_service.py:66-92`. A `@staticmethod` on `ProjectAssignmentService` (class at line 63; module singleton `project_assignment_service` at the end of the file). It has no docstring and no type hints.
- `ProjectAssignmentService.remove_user_from_project`, line 694. Reached from the router `src/codemie/rest_api/routers/projects/membership.py:108-118` and from `src/codemie/service/user/user_access_service.py:214`.
- `src/codemie/service/project/project_service.py:784-785`, which removes the project creator.
- `personal_project_service.py:144` calls only `aremove_project`. It does not call the budget sync.
- `tests/codemie/service/project/test_project_assignment_service.py:1004` patches it with `side_effect=Exception("LiteLLM timeout")` (`test_provider_delete_failure_is_fail_open`).
- Provider failure on member removal does not block the DB change: the exception is swallowed and the allocation is soft-deleted anyway (`project_assignment_service.py:203-215`). The same behaviour is pinned by the test at `test_project_assignment_service.py:994`. No reason is written down in a comment. The alternative (propagate and abort) was not chosen.
