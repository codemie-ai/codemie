# Technical Research

**Task**: keycloak sync job idp-sync api cronjob
**Generated**: 2026-11-10
**Research path**: filesystem

---

## 1. Original Context

"https://jiraeu.epam.com/browse/EPMCDME-16897 мы сделали api, теперь надо переделать уже существующую job, которая будет использовать этот api"

Ticket EPMCDME-16897 "IdP-Managed Users — Customer-side Keycloak sync job calling the management API" (sub-task of EPMCDME-15435; depends on EPMCDME-16895 which is DONE on this branch: the management API /v1/idp-sync/).
Ticket context: the current branch's sync reads Keycloak correctly (enabled users by page with one reused token, the admin realm role's members, the `projects/` and `project-admins/` group trees and their members) and then writes straight into the platform database. The reading code depends on the platform's Keycloak client and configuration module and does not resolve outside the platform packages as is. The platform offers a service-account management API with idempotent create, assign, unassign and deactivate operations and reads of one user and a paginated user list that includes deactivated users; its request/response models are the contract (see src/codemie/rest_api/models/idp_sync.py, src/codemie/rest_api/routers/idp_sync.py, docs: this branch).
Story: As a platform operator at the customer, I want a scheduled job that reconciles Keycloak with the platform through the management API, so that we control when and how identities are reconciled and the platform never receives direct database writes.
Acceptance criteria (ticket):
- The job reads from Keycloak the enabled users with their profile, the admin realm role, and project membership and project-admin right from the group paths under `projects/` and `project-admins/`; reads platform state through the management API; applies only the differences through the management API; never connects to the platform database.
- Keycloak and platform already agree -> no write.
- Pre-existing personal projects and platform users absent from or disabled in Keycloak are left untouched. Deactivation is only called when the operator turns it on explicitly (default off).
- Keycloak unreachable/errors/returns no users/not configured -> write nothing, run reported as skipped. Management API unreachable or refuses the account -> job stops, run reported failed.
- One user's reconciliation fails or is rejected (e.g. demotion of the last administrator) -> all other users reconciled; run reports the failure count and a failed result.
- Dry-run mode: reports every change it would make, writes nothing.
- Every run logs counts of users created, updated, reactivated, deactivated, memberships added/removed, admin-right changes, failures and skip status, and exits with a result a scheduler can alert on. Two runs never overlap; a full walk of 60,000 users finishes within the schedule.
- The job builds its own container image and ships a CronJob manifest, and it imports no platform package.
- Docs: an operator can build and schedule it, configure the Keycloak client it reads with and the service account it writes with, run a dry run and a first live sync before turning the switch on, and roll it back.
Out of scope: any change to the platform; a policy for deactivating users who disappear from Keycloak; real-time/webhook sync; sync run history in the platform.
IMPORTANT CONFLICT TO RESEARCH, NOT RESOLVE: the ticket says the job lives in the CUSTOMER'S OWN repository (outside this workspace, greenfield) and imports no platform package. The user says the existing job lives in THIS repository (src/codemie/enterprise/sync/ {coordinator.py, entrypoint.py}, Dockerfile.sync, deploy-templates/templates/sync-cronjob.yaml, values.yaml keycloak/keycloakSync blocks, config KEYCLOAK_SYNC_*, docs/operations/idp-managed-sync.md, tests/codemie/enterprise/sync/) and is to be reworked here. (Remaining research instructions: document what the job imports from the platform, what is reusable, what must become HTTP calls to /v1/idp-sync/, what it takes to import no platform package while staying in this repo, how the token for the management API is obtained, overlap prevention, dry-run/skip/failure semantics, existing tests.)

---

## 2. Codebase Findings

### Existing Implementations

**Purpose A: read Keycloak** — all in `src/codemie/enterprise/sync/coordinator.py` (552 lines), no platform import except `config`:
- `_fetch_users` (:168) pages `GET /users?enabled=true&briefRepresentation=false`; stops on partial page; raises past `_MAX_PAGES=1000`. Sequential, page size = `KEYCLOAK_SYNC_BATCH_SIZE` (default 100).
- `_get_all` (:186) generic paged GET (1000/page).
- `_fetch_admin_user_ids` (:204) `GET /roles/{ADMIN_ROLE_NAME}/users`; returns None on any failure (admin flags then left unchanged). Direct holders only.
- `_subgroups` (:224) `/groups/{id}/children`, falls back to embedded `subGroups` on 404 (KC < 23). `_fetch_group_assignments` (:243) walks top-level `/groups`, matching names `projects` and `project-admins`; subgroup name = project; members via `/groups/{id}/members`. Returns None on any failure (memberships left alone). Only one level of subgroups.
- `_kc_profile` (:111) / `_is_admin` (:133): email required (else skipped), name = first+last or username, picture = `attributes.picture[0]`. Admin = realm role OR legacy attribute `roles` contains `platform_admin` OR `config.ADMIN_USER_ID` (bootstrap admin). Also reads the legacy `applications` / `applications_admin` user attributes (:466-469), merged with group assignments (ticket mentions only group paths).
- Keycloak HTTP: `codemie_tools.access_management.keycloak.keycloak_client.KeycloakClient` (`src/codemie_tools/.../keycloak_client.py`): sync `httpx`, `execute_request(method, relative_url, params)` against `{base}/admin/realms/{realm}{url}`; token via `POST /realms/{realm}/protocol/openid-connect/token` with `grant_type=client_credentials`, cached in `_access_token`, one re-acquire+retry on 401; errors raised as `langchain_core.tools.ToolException` (so the module imports `langchain_core`). Used by sync from an async coordinator but called synchronously (blocks the loop, harmless here).

**Purpose B: write the platform (to be replaced)** — in coordinator, via injected `CodemieSyncDeps` (:52) repositories: `user_repository` (`aget_by_id/aget_by_email/acreate/aupdate`), `user_project_repository` (`aget_by_user_id/aadd_project/aremove_project`, direct `session.add` of `is_project_admin`), `application_repository` (`aget_by_name/acreate`), plus lazy `from codemie.rest_api.models.user_management import UserDB` (:295). `user_kb_repository`, `personal_project_service`, `dynamic_config_service` are carried in deps but unused/None. Reconcile logic: `_reconcile_user` (:324), `_reconcile_memberships` (:373), `_reconcile_user_memberships` (:454), `_sync_user` (:477, per-user savepoint). Rules: lookup by id first, then email (email owned by another id -> skipped with warning); reactivation sets `is_active=True, deleted_at=None`; maintainers never demoted (`is_maintainer`); personal projects (Application.project_type=="personal") never removed; memberships only reconciled when Keycloak names >=1 project for the user (empty never wipes); missing Application created on assign; admin flags updated on existing memberships. Never deactivates anyone.

**Purpose C: DB session / entrypoint** — `entrypoint.py` (141 lines): builds asyncpg engine from `config.PG_URL` or `POSTGRES_*` (`_asyncpg_url`, `get_async_session`), wires the four repository singletons from `codemie.repository.*`, builds `KeycloakClient` from `config.KEYCLOAK_ADMIN_*`, runs `coordinator.run(batch_size, dry_run)`, `SystemExit(1)` for status `error`/`partial`. `_validate_env` requires `KEYCLOAK_ADMIN_URL/REALM/CLIENT_ID/CLIENT_SECRET` and `POSTGRES_HOST` (unless `PG_URL`); exits 1 listing missing. `KEYCLOAK_SYNC_ENABLED=false` -> logs a warning and returns (exit 0, no write).

**Purpose D: overlap prevention** — Today: PostgreSQL advisory lock via `codemie.utils.leader_lock.async_leader_lock(_LOCK_ID)` (coordinator :27, :34 `_LOCK_ID = 0x4B435359_4E4300`), `src/codemie/utils/leader_lock.py` imports `codemie.clients.postgres.PostgresClient` and `codemie.configs.logger` (needs the platform DB — unusable in a no-DB job). Lock held -> status `skipped`, exit 0. Plus CronJob `concurrencyPolicy: Forbid` (`deploy-templates/templates/sync-cronjob.yaml`), `activeDeadlineSeconds` (default 300), `backoffLimit: 2`, `restartPolicy: OnFailure`.

**Purpose E: run result semantics (`SyncResult`, :62)** — status `ok` / `skipped` (= lock held, NOT Keycloak problems) / `partial` (some users failed) / `error` (Keycloak unreadable, no users, every user failed, DB failure). Counters: created, updated, reactivated, memberships_added/removed, admin_flags_updated, user_failures. No `deactivated` counter. Log line `sync_complete: ...` (:540) and `sync_run_complete: status=`. Exit code 0 for ok/skipped, 1 for partial/error. Dry run: `dry_run=True` -> same walk, `dry_run:` log lines per would-be change, counters still count planned changes, no writes (`_create_user` returns `SimpleNamespace(id=...)` placeholder). Note vs ticket: today Keycloak failure = `error`/exit 1; the ticket wants "skipped" for Keycloak unreachable/empty/not configured, and "failed" for management-API failure (different mapping).

**Purpose F: management API the job would call** (`src/codemie/rest_api/routers/idp_sync.py`, models `src/codemie/rest_api/models/idp_sync.py`), prefix `/v1/idp-sync`, router-level `require_idp_sync_account`:
- `PUT /users/{id}` body `IdpUserUpsertRequest{id(UUID, must equal path), email(3-255), username(1-255), name(1-255), is_admin}` -> `IdpUserResponse{id,is_active,email,username,name|None,is_admin,memberships[{project_name,is_project_admin,is_personal}]}`; idempotent; creates without personal project; reactivates without restoring memberships; 400 on id mismatch.
- `GET /users/{id}`; `GET /users?page=0..&per_page<=100` -> `IdpUserPage{items,total,page,per_page}`, includes deactivated, ordered by id. 60,000 users = 600 list calls at max page size.
- `PUT /users/{id}/projects/{project}?is_project_admin=bool` (idempotent; creates the project; updates admin flag; personal projects immutable) -> user; `DELETE ...` 204 (idempotent; personal and project creator cannot be removed); `POST /users/{id}/deactivate` 204 (idempotent; last admin and the sync account itself cannot be deactivated).
- Last-admin demotion rejected with 409 "Cannot demote the last administrator" (`src/codemie/service/user/idp_sync_service.py:169-174`). `invalidate_user_from_cache` runs per write.
- Auth guard `src/codemie/rest_api/security/idp_sync_guard.py`: allowed only if `user.id == config.IDP_SYNC_SERVICE_ACCOUNT_ID` (setting at `config.py:242`, default "" = API closed to everyone, 403) AND `user.user_type == "service_account"` (`user_type_validator.SERVICE_ACCOUNT_USER_TYPE`). The same account is refused (403) on every authenticated route outside `/v1/idp-sync/`. No model field to express request-level "no change" — the job must diff against `GET /users` itself.

**Purpose G: token for the management API** — nothing in the sync obtains one today (the job talks to Keycloak and DB only). The only existing token code is `KeycloakClient._get_admin_token` (client_credentials against the Keycloak realm token endpoint), which targets the Admin API. For the platform API the guard needs `authenticate` to resolve the bearer to a User whose id == `IDP_SYNC_SERVICE_ACCOUNT_ID` with `user_type=service_account` (claim is validated by `validate_user_type`, `src/codemie/rest_api/security/user_type_validator.py`). I did not trace how the Keycloak IdP maps token claims (`sub`, `user_type`) to User fields; unverified. No docs describing the service-account Keycloak client exist outside `docs/superpowers/tasks/2026-11-10-rbac-api-updates/` (spec/plan of 16895).

**Other touched code** (not named above): `Dockerfile.sync` (90 lines): two-stage build, `poetry install --only main` from the root `pyproject.toml`/`poetry.lock` (full platform dependency set), copies `src/codemie/{enterprise/sync,configs,rest_api/models,repository,clients,core,service,triggers,utils/leader_lock.py,__init__}`, `src/codemie_tools/`, `config/`; `PYTHONPATH=/app/src`; build-time `python -c "import codemie.enterprise.sync.entrypoint"`; `CMD python -m codemie.enterprise.sync.entrypoint`. Its own comment says slimming needs import coupling removed first. `deploy-templates/templates/sync-cronjob.yaml` (82 lines) and `deploy-templates/values.yaml` `keycloak:` (:203, `adminUrl`, `adminRealm`) and `keycloakSync:` (:211-249) blocks: `enabled,schedule("*/10 * * * *"),dryRun,batchSize,credentialsSecret("oauth2-proxy", keys client-id/client-secret),image.{repository,pullPolicy,tag},database.{host,port,name,credentialsSecret},customerConfigMap,resources,successfulJobsHistoryLimit,failedJobsHistoryLimit,ttlSecondsAfterFinished(commented),activeDeadlineSeconds(300)`. The CronJob also uses the chart's `codemie.serviceAccountName`, `imagePullSecrets`, mounts the optional customer ConfigMap at `/app/config/customer`, and sets env `KEYCLOAK_ADMIN_*`, `KEYCLOAK_SYNC_ENABLED/DRY_RUN/BATCH_SIZE`, `POSTGRES_*`. `src/codemie/configs/config.py:277-281`: `KEYCLOAK_SYNC_ENABLED` (False), `KEYCLOAK_SYNC_SCHEDULE` (declared; grep shows no consumer outside config), `KEYCLOAK_SYNC_BATCH_SIZE`, `KEYCLOAK_SYNC_DRY_RUN`. `docs/operations/idp-managed-sync.md` (153 lines) documents the DB-writing job (DB secret, `realm-management` roles `view-users, query-users, query-groups, view-realm`, dry run, rollback by suspending CronJob, status table, deprovisioning "out of scope" via direct DB update).

### Decisions Already in the Code
- Keycloak is the source of truth for profile/admin/memberships; DB is never read-modify-write by anything else in the sync. Where the Keycloak read is incomplete the dependent part is skipped (admin set None, groups None) rather than treated as empty: `coordinator.py:95-103, 204-222, 243-277`.
- Never remove personal projects; never wipe memberships on empty Keycloak data; never demote maintainers (`:397-427`, `:153`, `:470`). The API enforces personal-project immutability itself, but "is_maintainer" and "empty means no change" exist only in the job (API response has no `is_maintainer`).
- Direct admin holders of the role only, no group/composite inheritance (documented in the guide).
- Identity matching: id first, email collisions skipped (`:339-352`); the API keys on id only, so email-collision handling would come back as an API error (unverified response code) or must be pre-detected from `GET /users`.
- Per-user failure isolation via DB savepoint (`:480`); with HTTP this becomes per-user try/except.
- Stored -> read direction: platform memberships are read as `IdpUserResponse.memberships` (includes personal flag), which replaces the Application lookup (`project_type=="personal"`).
- Python 3.12, async coordinator, tests via pytest-asyncio; `httpx` is already the HTTP library (`pyproject.toml:112`).

### Task Statements Checked Against the Code
- "current branch's sync reads Keycloak correctly ... one reused token": holds (token cached in `KeycloakClient._access_token`, 401 retry).
- "reading code depends on the platform's Keycloak client and configuration module and does not resolve outside the platform packages": holds — `coordinator.py` imports `codemie.configs.config` (for `ADMIN_ROLE_NAME`, `ADMIN_USER_ID`) and `codemie.utils.leader_lock`; `entrypoint.py` imports `codemie.configs`, four `codemie.repository.*` singletons, `codemie_tools...KeycloakClient`, sqlalchemy/sqlmodel.
- "job lives in the customer's own repository (outside this workspace, greenfield)" vs user "existing job lives in THIS repository": the in-repo job exists exactly as the user lists (all paths confirmed). No customer repository is reachable from this workspace; nothing in the repo confirms or refutes the ticket's repository claim. Unverified, left as conflict. Git log shows `11b01c281 EPMCDME-16895: Restore the in-repo Keycloak sync job`.
- "Keycloak unreachable ... run reported as skipped": contradicted by current code (these are `error`, exit 1; `skipped` means lock held).
- "Two runs never overlap": currently kept by the PG advisory lock (needs platform DB) plus `concurrencyPolicy: Forbid` — the lock mechanism cannot stay under "never connects to the platform database"; CronJob `Forbid` is already in place.
- "a full walk of 60,000 users finishes within the schedule": today `activeDeadlineSeconds` defaults to 300 and schedule 10 minutes; Keycloak read is sequential 600 pages at batch 100 (`_MAX_PAGES` 1000 pages = 100,000 users max at batch 100, would raise beyond); platform list is 100/page max. No timing evidence in repo.
- "reads of one user and a paginated user list": holds (routers above). "Pre-existing personal projects left untouched": API refuses to change personal projects; job also filters them today.
- "docs: this branch": API docs for operators are not present in `docs/` (only superpowers task artifacts of 16895).

### Consumers of Changed Contracts
- `codemie.enterprise.sync.*`: imported only by tests, `Dockerfile.sync` (COPY + `python -m`), and nothing else in `src` (grep for `enterprise.sync` / `Dockerfile.sync` outside docs/superpowers hits only those tests, the Dockerfile, `entrypoint.py` and `values.yaml`). `KEYCLOAK_SYNC_*` config used only by `entrypoint.py` (and `KEYCLOAK_SYNC_SCHEDULE` by nobody found). `ADMIN_USER_ID` / `ADMIN_ROLE_NAME` are platform config also used by the platform itself; the job would need its own copies. `IDP_SYNC_SERVICE_ACCOUNT_ID` only on the platform side.
- `keycloakSync.*` / `keycloak.*` Helm values are consumed by `sync-cronjob.yaml` only. No CI/Makefile/pyproject reference to the sync files was found (`.github`/pipeline files not present in the repo per AGENTS.md).
- `KeycloakClient` is shared with the platform's agent tools (`src/codemie_tools/access_management/keycloak/`); changing it affects other consumers, so the job should not edit it.

### Architecture and Layers Affected
Standalone batch job (not part of the FastAPI app): entrypoint -> coordinator -> (Keycloak admin REST client) -> (platform repositories/DB today; HTTP management API in the rework). Packaging layer: `Dockerfile.sync`, Helm CronJob + values, operator doc.

### Integration Points
Keycloak Admin REST + token endpoint (realm in `keycloak.adminRealm`); platform `/v1/idp-sync/*` (new HTTP dependency: base URL, token acquisition); PostgreSQL (to be removed). Platform-side config the operator must align: `IDP_SYNC_SERVICE_ACCOUNT_ID` on the API.

**Platform imports of the existing job (full list):**
| File | Import | Fate |
|---|---|---|
| coordinator.py | `codemie.configs.config` (ADMIN_ROLE_NAME, ADMIN_USER_ID) | replace with job-local env settings |
| coordinator.py | `codemie.utils.leader_lock.async_leader_lock` (-> `codemie.clients.postgres`, `codemie.configs.logger`) | drop (DB); rely on CronJob `Forbid` or another non-DB mechanism (design) |
| coordinator.py (lazy) | `codemie.rest_api.models.user_management.UserDB` | drop |
| entrypoint.py | `codemie.configs.config` | job-local settings |
| entrypoint.py | `codemie.repository.{application,user_kb,user_project,user}_repository` | drop; replaced by HTTP client |
| entrypoint.py | `codemie_tools.access_management.keycloak.keycloak_client.KeycloakClient` (+ `langchain_core`) | copy/reimplement locally (~100 lines, httpx only) |
| entrypoint.py | sqlalchemy asyncpg engine, sqlmodel AsyncSession | drop |

Reusable as-is (pure logic, no platform types): `_split_csv`, `_kc_profile` (needs ADMIN_USER_ID injected), `_fetch_users`, `_get_all`, `_fetch_admin_user_ids` (needs role name injected), `_subgroups`, `_group_member_ids`, `_fetch_group_assignments`, `_KeycloakSnapshot`, `_GroupAssignments`, `SyncResult` shape (needs `deactivated` and a skipped/failed mapping). To be replaced: everything in "platform writes" (`_get_application`, `_create_user`, `_update_user`, `_reconcile_*`, `_sync_user` DB/savepoint parts), `run` lock/DB sections, `CodemieSyncDeps`.

**Staying in this repo with no platform import (facts):** no existing standalone sub-project in this repo has its own pyproject for a job; `standalone/` holds an unrelated deployment (`Dockerfile`, `build-image.sh`, docker-compose). Root `pyproject.toml` is a single poetry project (python >=3.12,<3.14, `httpx ^0.28.1`, `pydantic ^2.9.2`). Importing `codemie.*` currently pulls the full app dependency set (per `Dockerfile.sync` comment). A job without platform imports needs only `httpx` (+ optionally `pydantic`/stdlib). A separate directory would need its own manifest (pyproject or requirements), its own Dockerfile with a build context limited to that directory, and its own tests location (root `pytest.ini` / coverage / Sonar `sonar-project.properties` and `make ruff` may pick up a new directory — not checked). The Helm chart in `deploy-templates/` is the existing home of the CronJob; the ticket says the job ships its own CronJob manifest.

### Patterns and Conventions
Typed `@dataclass` results; module-level `logger = logging.getLogger(__name__)`; per-item error isolation; log keys `sync_complete:`, `sync_user_failed:`, `dry_run:`; Apache license header on source files; Helm values with `required` guards for mandatory keys; ruff line length per repo (see `.ai-run/guides/standards/code-quality.md`, not read).

---

## 3. Documentation Findings

### Guides and Architecture Docs
`.ai-run/guides/` exists (AGENTS.md index); none of the guides listed is specific to the sync job. Not read in depth (config/integration guides may be relevant: `development/configuration-patterns.md`, `integration/external-services.md`). `docs/operations/idp-managed-sync.md` is the operator guide for the current DB-writing job and would be stale in the sections: DB secret/values, `dryRun` "without writing to the database", status table (`skipped` = lock held), rollback (suspend, "keeps writing to the database"), "Deprovisioning (out of scope)" (suggests direct DB edits). No guide documents the management API or the service-account Keycloak client.

### Architectural Decisions
No ADR. Inline comments: lock ID rationale (`coordinator.py:31-34`), `CR-007/008/009/013/015` review markers, Dockerfile comment on import coupling (separate ticket).

### Derived Conventions
Config via env vars validated at start (`_validate_env`), Secret refs for credentials in Helm, `concurrencyPolicy: Forbid`, exit code 1 on failure.

---

## 4. Testing Landscape

### Existing Coverage
`tests/codemie/enterprise/sync/` (1532 lines): `_fakes.py` (`FakeKeycloak` serving users/role/groups with paging, legacy subgroups, `fail_on`; `lock_acquired`; `session_with_savepoint`; coordinator builders with mocked repositories), `test_coordinator_keycloak_reads.py` (228; paging, role, groups, partial-read fallbacks), `test_coordinator_users.py` (328; create/update/reactivate, email collision, admin flag, maintainer), `test_coordinator_memberships.py` (204), `test_coordinator_personal_projects.py` (75), `test_coordinator_dryrun.py` (150), `test_coordinator_lifecycle.py` (144; lock skip, KC unreachable/no users -> error, DB unavailable, paging limit, batch_size<1, all/some users fail), `test_entrypoint.py` (246; exit codes, `_validate_env`, `_asyncpg_url`, engine disposal, disabled flag).

### Testing Framework and Patterns
pytest + pytest-asyncio, `unittest.mock` (AsyncMock/MagicMock/patch), hand-written fakes. Tests import `codemie.*` (e.g. `from codemie.configs import config`), so they stay platform-coupled; the Keycloak-read tests and `FakeKeycloak` are reusable for the read half. DB-oriented tests (users, memberships, personal projects, lifecycle DB/lock cases, entrypoint DB tests) would be replaced by HTTP-fake (e.g. `httpx.MockTransport`) tests.

### Coverage Gaps
No tests for any HTTP call to `/v1/idp-sync/`, token acquisition for the platform API, deactivation, HTTP error mapping (401/403/409), 60,000-user volume/time. No test of the Docker image or Helm template (no helm-unittest found).

---

## 5. Configuration and Environment

### Environment Variables
Current: `KEYCLOAK_ADMIN_URL`, `KEYCLOAK_ADMIN_REALM`, `KEYCLOAK_ADMIN_CLIENT_ID`, `KEYCLOAK_ADMIN_CLIENT_SECRET` (required), `POSTGRES_HOST/PORT/DB/USER/PASSWORD`/`PG_URL`, `KEYCLOAK_SYNC_ENABLED`, `KEYCLOAK_SYNC_DRY_RUN`, `KEYCLOAK_SYNC_BATCH_SIZE`; implicit via platform config: `ADMIN_USER_ID`, `ADMIN_ROLE_NAME`, `DEFAULT_DB_SCHEMA`. Platform side: `IDP_SYNC_SERVICE_ACCOUNT_ID`.

### Configuration Files
`src/codemie/configs/config.py` (pydantic settings, loads `config/` YAML at import: the Dockerfile copies `config/` for this reason), `deploy-templates/values.yaml`, `deploy-templates/templates/sync-cronjob.yaml`.

### Feature Flags and Deployment Concerns
`KEYCLOAK_SYNC_ENABLED` (job-level switch), `keycloakSync.enabled` (Helm), `IDP_MANAGED_ENABLED` on the API (docs). Credentials: the Keycloak client secret comes from the `oauth2-proxy` Secret (client-id/client-secret) — the same secret the platform's oauth2-proxy uses; a separate service-account client secret for the management API would be a second Secret (none exists). Image: built from `Dockerfile.sync`, pushed to `keycloakSync.image.repository`; no build script/CI found for it.

---

## 6. Risk Indicators

- Repository conflict unresolved: ticket says customer repo/greenfield and "imports no platform package"; user says rework here. The ticket's CronJob/image/docs ownership depends on that answer.
- Speculative: "no platform package" inside this repo implies a self-contained directory (own manifest, Dockerfile context, tests, config); ruff/pytest/Sonar/CI config may pick it up or ignore it (not checked).
- Speculative: HTTP-backed rewrite replaces ~60% of coordinator.py (writes, reconcile, lock) and most tests; read half reusable.
- Overlap protection: current mechanism is a platform-DB advisory lock (`leader_lock`), incompatible with "never connects to the platform database"; only `concurrencyPolicy: Forbid` remains (see Task Statements).
- Status mapping differs from ticket (Keycloak failure = error/exit 1 today; ticket wants skipped; lock-held `skipped` has no equivalent without the lock).
- Management-API token flow is not implemented or documented anywhere: Keycloak claim mapping for `sub`/`user_type=service_account` untraced; separate Keycloak client and Secret needed (Speculative on secret layout).
- Volume: 60,000 users -> ~600 list calls (max 100/page) plus Keycloak 600 pages at batch 100; `_MAX_PAGES=1000` caps `/users` at 100,000 users at batch 100; default `activeDeadlineSeconds=300` and `backoffLimit: 2` with `restartPolicy: OnFailure` retries a failed run. Writes per user are individual HTTP calls (upsert, assign, unassign).
- API has no "unchanged" signal: the job must compare against `GET /users` for every user; admin demotion of last admin returns 409 (per-user failure).
- Email-collision and maintainer semantics of the current job are not expressed in the API contract.
- Docs `idp-managed-sync.md` becomes inaccurate in most sections; operator-facing API/service-account docs absent.
- `KeycloakClient` is shared with agent tools — copying not editing; `KEYCLOAK_SYNC_SCHEDULE` config unused.

---

## 7. Summary for Complexity Assessment

The existing in-repo job (`src/codemie/enterprise/sync/`, 693 lines, plus `Dockerfile.sync`, Helm CronJob/values, a 153-line operator guide and 1532 lines of tests) already implements the Keycloak read side with no platform coupling beyond `config` and `KeycloakClient`; its write side is entirely platform repositories inside a DB session and a PG advisory lock. Reworking means replacing the write path, lock, DB session and image with an HTTP client for `/v1/idp-sync/` (6 endpoints, models available), a management-API token flow (not present anywhere), a deactivation option, new result/exit-code semantics, and a self-contained package/image/manifest/doc set.

Technical novelty is moderate: no new framework (httpx, pytest already used), but there is no precedent in the repo for a platform-free sub-project, and the token flow and last-admin/409 handling are new. Test posture: strong coverage of the read half and DB logic (reusable fakes), none of the HTTP side; DB-bound tests are discarded. Key unknowns: where the job lives (ticket vs. user), the service-account claim mapping, overlap prevention without the DB, and runtime for 60,000 users.

---

## 8. External References

None named by the task as a readable path. The Jira ticket URL (https://jiraeu.epam.com/browse/EPMCDME-16897) was not fetched; the ticket text supplied in task_context was used. The "customer's own repository" is not identified by any path and is not reachable from this workspace.
