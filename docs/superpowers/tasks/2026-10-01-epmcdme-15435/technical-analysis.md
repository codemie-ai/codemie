# Technical Research

**Task**: keycloak idp sync user-management
**Generated**: 2026-10-01T00:00:00Z
**Research path**: filesystem

---

## 1. Original Context

IdP-Managed Users — Backend (EPMCDME-15435): Implement a feature switch that rejects all in-platform user/membership mutations when IdP-managed mode is on. Implement a continuous Keycloak-to-platform sync job that reconciles users, memberships, and budgets from Keycloak on a schedule (default every 10 minutes). Package the sync as a dedicated minimal Docker image with only the sync process and the platform modules it needs (no web server, AI/LLM stack, search engine, or cache client).

---

## 2. Codebase Findings

### Existing Implementations

- `src/codemie_tools/access_management/keycloak/keycloak_client.py` — `KeycloakClient`: synchronous httpx client for Keycloak Admin API; acquires a fresh client-credentials token per request; `execute_request(method, relative_url, params)` and `health_check()`. Used by agent tools only today.
- `src/codemie_tools/access_management/keycloak/tools.py`, `models.py`, `tools_vars.py` — agent-facing LangChain tools wrapping `KeycloakClient`.
- `src/codemie/enterprise/migration/coordinator.py` — `run_keycloak_migration()`: one-time startup migration adapter that builds `CodemieMigrationDeps` and delegates to `codemie_enterprise.migration.KeycloakMigrationCoordinator`. Wires `user_repository`, `user_project_repository`, `user_kb_repository`, `application_repository`, `PersonalProjectService`, `DynamicConfigService`, and a PostgreSQL advisory lock (lock id `7734269302`).
- `src/codemie/rest_api/routers/user_management_router.py` — FastAPI router at `/v1/admin/users`; all CRUD and access-management endpoints (10+ mutation paths: POST, PUT, DELETE for users, projects, KBs, budgets). All are already gated on `config.ENABLE_USER_MANAGEMENT`. The one existing IDP check is `config.IDP_PROVIDER != "local"` on the `create_user` endpoint and on `email`/`user_type` edits in the service.
- `src/codemie/service/user/user_management_service.py` — `UserManagementService`: create/update/deactivate users. `update_user_fields()` rejects `email` and `user_type` mutations when `IDP_PROVIDER != "local"`.
- `src/codemie/service/user/authentication_service.py` — `AuthenticationService`: handles IDP first-login (`create_user_from_idp`), profile sync (`sync_idp_user_profile`), budget bootstrap (`_sync_budget_for_idp_projects`).
- `src/codemie/service/user/user_access_service.py` — `UserAccessService`: grant/revoke project and KB access; no IDP-managed guard today.
- `src/codemie/service/project/project_assignment_service.py` — `ProjectAssignmentService`: `_sync_project_budget_member_added()` called on project grant; runs sync budget logic via thread-pool + main event loop bridge.
- `src/codemie/rest_api/models/user_management.py` — ORM tables: `UserDB` (users), `UserProject` (user_projects), `UserKnowledgeBase` (user_knowledge_bases). DTOs for all request/response shapes.

### Architecture and Layers Affected

| Layer | Component |
|---|---|
| Config | `src/codemie/configs/config.py` — new env vars needed |
| Router (API) | `src/codemie/rest_api/routers/user_management_router.py` — mutation guard |
| Service | `user_management_service.py`, `user_access_service.py`, `authentication_service.py` |
| Scheduler infra | New scheduler class (pattern: `StaleDatasourceScheduler`) |
| External client | `KeycloakClient` (already exists; needs async variant or wrapper for sync job) |
| Enterprise adapter | `src/codemie/enterprise/migration/coordinator.py` (reuse/extend for recurring sync) |
| Docker | New `Dockerfile.sync` (no precedent in repo) |
| Alembic | Possibly none; DB schema for users/projects already exists |
| App startup | `src/codemie/rest_api/main.py` — scheduler registration |

### Integration Points

- **Keycloak Admin API** (`/admin/realms/{realm}/users`): polled by `KeycloakClient`; credentials via `KEYCLOAK_ADMIN_CLIENT_ID` / `KEYCLOAK_ADMIN_CLIENT_SECRET`.
- **PostgreSQL** (asyncpg + SQLModel): `user_repository`, `user_project_repository`, `user_kb_repository`, `application_repository`, advisory-lock via `async_leader_lock`.
- **`codemie_enterprise` package** (optional, GCP Artifact Registry): `KeycloakMigrationCoordinator`, `MigrationConfig`. The sync job would import from this package; it is gated behind `INSTALL_ENTERPRISE=true` in the Dockerfile.
- **Budget subsystem**: `ProjectAssignmentService._sync_project_budget_member_added` — requires the main app event loop bridge if called from a sync context.
- **`DynamicConfigService`**: used by the migration coordinator for lock state tracking.

### Patterns and Conventions

- **Feature flags**: boolean config vars (`ENABLE_USER_MANAGEMENT`, `KEYCLOAK_MIGRATION_ENABLED`, `STALE_DATASOURCE_ENABLED`); checked at router or service entry; defined in `Config(BaseSettings)` with defaults.
- **Scheduler pattern**: each recurring job is a class taking an `AsyncIOScheduler` in its constructor; `start()` checks a feature-flag, registers a `CronTrigger`, and does not start the scheduler if already running; the job coroutine acquires `async_leader_lock` before doing work. All schedulers registered in `main.py` lifespan via `_setup_*_scheduler(app)` helpers.
- **Advisory-lock multi-pod safety**: `src/codemie/utils/leader_lock.py` (`LeaderLockContext`, `async_leader_lock`) — PostgreSQL `pg_try_advisory_lock`.
- **Enterprise adapter boundary**: `src/codemie/enterprise/migration/coordinator.py` wires concrete codemie repos into the protocol expected by `codemie_enterprise.migration`; the same deps pattern would be reused for a sync coordinator.
- **IDP mode guard** (partial existing): `config.IDP_PROVIDER != "local"` in `update_user_fields`; this pattern would be extended to a new `config.IDP_MANAGED_ENABLED` flag across all mutation endpoints.

---

## 3. Documentation Findings

### Guides and Architecture Docs

- `.ai-run/guides/architecture/layered-architecture.md` — enforces API → Service → Repository; startup side-effects explicit in main.py.
- `.ai-run/guides/architecture/project-structure.md` — integration logic belongs in services or `codemie_tools` adapters; new top-level packages discouraged.
- `.ai-run/guides/development/configuration-patterns.md` — feature flags gated at assembly points; config values via `src/codemie/configs/config.py`.
- `.ai-run/guides/integration/external-services.md` — external API details kept out of routers; credentials through config.
- `.ai-run/guides/testing/testing-patterns.md` — tests placed under `tests/codemie/`; mock provider boundaries; seam tests for policy helpers.

### Architectural Decisions

- Optional user-management routers explicitly gated in `main.py` (line 706 per guide).
- The enterprise migration coordinator uses a **protocol/deps injection** pattern so the enterprise package has zero dependency on codemie internals.
- Schedulers are **not** registered with a shared global scheduler instance; each feature gets its own `AsyncIOScheduler` instance, attached to `app.state`.

### Derived Conventions

- A new feature switch (`IDP_MANAGED_ENABLED`) should follow the `ENABLE_USER_MANAGEMENT` pattern: boolean in `Config`, default `False`, checked at the router entry of each mutation endpoint before delegating to the service.
- A new sync scheduler class should follow `StaleDatasourceScheduler` verbatim: `__init__(self, scheduler)`, `start()` with enabled-flag guard, `_run_*` async method with `async_leader_lock`, `stop()`.
- Sync-job config vars (`KEYCLOAK_SYNC_ENABLED`, `KEYCLOAK_SYNC_SCHEDULE`) should be grouped near the existing `KEYCLOAK_MIGRATION_*` block in `config.py`.

---

## 4. Testing Landscape

### Existing Coverage

- `tests/codemie/rest_api/test_main_keycloak_migration.py` — unit tests for `_run_keycloak_migration()` conditions (flag combinations).
- `tests/codemie/service/user/test_authentication_service.py` — `AuthenticationService` unit tests (AsyncMock pattern, `_clear_auth_cache` autouse fixture).
- `tests/unit/service/leaderboard/test_scheduler.py` — `LeaderboardScheduler` unit tests (mock APScheduler, `MagicMock(running=False)`, `@patch(f"{MODULE}.config")`).
- `tests/codemie/repository/test_user_repository.py`, `test_user_project_repository_*.py`, `test_user_kb_repository.py` — repository-layer tests.

### Testing Framework and Patterns

- **Framework**: pytest with `pytest-asyncio`; `@pytest.mark.asyncio` for async tests.
- **Mocks**: `unittest.mock.AsyncMock`, `MagicMock`, `@patch` decorator; module-level `MODULE = "codemie.service.*.module"` constant for patch targets.
- **Fixtures**: `autouse` cache-clear fixtures; `@pytest.fixture` for scheduler mocks.
- **No integration tests** found that spin up a real DB or Keycloak server.

### Coverage Gaps

- No tests for mutation endpoints rejecting requests under an IDP-managed mode flag.
- No tests for a Keycloak sync scheduler class (`start()`, `_run_sync()`, leader-lock skip path).
- No tests for the sync reconciliation logic (user create/update/deactivate, membership add/remove, budget sync).
- No tests for the dedicated sync Docker entrypoint.
- No tests for `user_access_service` project/KB grant/revoke under IDP-managed guard.

---

## 5. Configuration and Environment

### Environment Variables

Existing variables relevant to this feature:

| Variable | Default | Role |
|---|---|---|
| `IDP_PROVIDER` | `"local"` | Selects auth provider; sync job requires `"keycloak"` |
| `ENABLE_USER_MANAGEMENT` | `False` | Master switch for user management system |
| `KEYCLOAK_MIGRATION_ENABLED` | `False` | One-time migration toggle |
| `KEYCLOAK_ADMIN_URL` | `""` | Keycloak base URL |
| `KEYCLOAK_ADMIN_REALM` | `""` | Realm to sync |
| `KEYCLOAK_ADMIN_CLIENT_ID` | `""` | Service-account client ID |
| `KEYCLOAK_ADMIN_CLIENT_SECRET` | `""` | Service-account client secret |
| `KEYCLOAK_MIGRATION_BATCH_SIZE` | `100` | Users per page |
| `POSTGRES_HOST/PORT/DB/USER/PASSWORD` | `localhost/5432/…` | DB connection |
| `PG_URL` | `""` | Override DSN |

New variables the task implies (not yet present):

| Variable | Proposed Default | Role |
|---|---|---|
| `IDP_MANAGED_ENABLED` | `False` | Mutation reject switch |
| `KEYCLOAK_SYNC_ENABLED` | `False` | Sync job on/off |
| `KEYCLOAK_SYNC_SCHEDULE` | `"*/10 * * * *"` | Cron schedule (10-min default) |
| `KEYCLOAK_SYNC_BATCH_SIZE` | `100` | Users per Keycloak page |

### Configuration Files

- `src/codemie/configs/config.py` — single `Config(BaseSettings)` class; all env vars loaded here.
- `.env.example` / `docker-compose.yml` — local dev overrides (Keycloak vars currently absent; would need adding).

### Feature Flags and Deployment Concerns

- The main `Dockerfile` runs `uvicorn codemie.rest_api.main:app`. The new sync image must use a different entrypoint (e.g., `python -m codemie.sync.entrypoint` or a script).
- The Dockerfile installs `poetry install --only main -E enterprise`; a sync-only image needs a new Poetry dependency group (`[tool.poetry.group.sync.dependencies]`) or a separate `extras` declaration to exclude Elasticsearch, LangChain, LiteLLM, NATS, Azure Storage, etc.
- Deploy templates (`deploy-templates/templates/`) currently have no `CronJob` or second `Deployment` manifest; the sync container would need a new Helm template or a separate `Deployment`/`CronJob`.
- `KEYCLOAK_ADMIN_CLIENT_SECRET` follows the existing secrets pattern (env var; no Vault reference pattern observed in this repo).

---

## 6. Risk Indicators

- **Dependency isolation for the sync image is uncharted**: no precedent exists for a second Docker target in this repo. The poetry.lock includes LangChain, Elasticsearch, and LiteLLM as deeply transitive deps; a minimal sync image needs a new Poetry extras group to avoid installing them. Getting this wrong produces a large, CVE-exposed image.
- **Mutation guard scope**: 10+ mutation endpoints in `user_management_router.py` plus first-login user creation in `authentication_service.create_user_from_idp` must be covered. Missing any one path leaves a data integrity hole when IDP-managed mode is active.
- **Sync reconciliation vs. migration overlap**: `coordinator.py` + `codemie_enterprise.migration.KeycloakMigrationCoordinator` already does a one-time reconciliation. A recurring sync needs idempotent upsert semantics; reusing the coordinator is tempting but the one-time flow may have non-idempotent steps (personal project creation, lock-state machine).
- **Budget sync complexity**: `ProjectAssignmentService._sync_project_budget_member_added` bridges the thread-pool/main-loop boundary (`asyncio.run_coroutine_threadsafe`). In a standalone sync process without an app loop, this pattern breaks; a direct async call path or refactor is needed.
- **Advisory-lock ID collision**: `coordinator.py` uses lock id `7734269302` (hash of "keycloak_migration"). A new sync job must use a distinct ID.
- **Keycloak Admin API pagination**: `KeycloakClient` issues one call at a time and re-authenticates per request. A sync job iterating thousands of users should reuse the token and paginate (`/users?first=0&max=100`). The current client does not support this natively.
- **Multi-pod safety tested only at unit level**: no integration test confirms advisory lock semantics under concurrency.
- **`codemie_enterprise` package version**: `coordinator.py` imports `codemie_enterprise.migration`; the sync job may need a newer version of that package. Version pinned at `2.3.44`; must verify the enterprise package exposes a recurring-sync coordinator or that a new one is needed in this repo.

---

## 7. Summary for Complexity Assessment

This task spans three distinct sub-features, each at a different complexity level. The **mutation guard** (IDP-managed feature switch) is bounded but broad: it requires a new `IDP_MANAGED_ENABLED` config flag and a guard added to every non-read endpoint in `user_management_router.py` (10+ mutation paths) plus the first-login creation path in `AuthenticationService`. The pattern already partially exists for `email`/`user_type` edits. The risk is coverage completeness, not design novelty.

The **Keycloak sync scheduler** is the most complex sub-feature. It requires a new scheduler class following the `StaleDatasourceScheduler` pattern (AsyncIOScheduler + CronTrigger + advisory lock), new config vars, and non-trivial reconciliation logic that mirrors but extends what the one-time migration coordinator already does. The budget-sync path adds a further integration point with `ProjectAssignmentService` whose thread-loop bridge was not designed for standalone operation. The `KeycloakClient` also needs pagination support (currently absent).

The **dedicated Docker image** is architecturally novel for this repo — no second image target exists. It requires a new Poetry dependency group excluding the web, AI/LLM, Elasticsearch, and NATS stacks, a new Dockerfile stage or file, and possibly a new Helm template. The risk is that a naive implementation installs the full monorepo dependency tree and defeats the purpose. All three sub-features together touch: config, service (three service classes), router (all mutation endpoints), external client, scheduler infrastructure, enterprise adapter, Dockerfile, and deployment manifests — a large surface across many layers.

---

## 8. External References

None named by the task.
