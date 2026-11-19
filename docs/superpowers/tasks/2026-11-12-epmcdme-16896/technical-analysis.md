# Technical Research

**Task**: keycloak idp-managed sync helm
**Generated**: 2026-11-12
**Research path**: filesystem

---

## 1. Original Context

Jira EPMCDME-16896 (https://jiraeu.epam.com/browse/EPMCDME-16896): "IdP-Managed Users — Remove in-repository Keycloak sync". The job it builds on is implemented in branch EPMCDME-16895; the current branch EPMCDME-16896 was cut from it and its MR will target EPMCDME-16895 (not main). Compare against `main` where the ticket says "as on the main branch" (use `git diff main...HEAD` / `git show main:<path>`).

Sub-task of EPMCDME-15435 after the architecture review of its merge requests: the sync moves to the customer's side as a client of the platform's management API, so everything that existed only for the in-repository sync leaves the platform repository. Repository: codemie. Independent of the management API sub-task; the customer-side job sub-task depends on the management API.

## Context
- The current branch adds a sync package (coordinator and entrypoint) with its tests, a dedicated sync image, a Helm CronJob template with its values block and Keycloak admin values, four sync configuration variables and an operator guide for the in-repository CronJob. Nothing else in the repository references them; CI builds no sync image.
- The branch also rewrote the shared Keycloak client used by an agent tool (token cache, retry on 401, query parameters, parsed JSON responses) and adapted the tool to it; the sync was the only other consumer.
- The switch, its 403 guard, the public configuration flag and the enterprise group parsing stay.

## Story
As a platform maintainer, I want the in-repository Keycloak sync and everything that existed only for it removed, and the operator documentation rewritten around the service-account model, so that the platform repository carries no customer-specific scheduling and no second write path to user data.

## Acceptance Criteria
- Given the platform repository after this sub-task, then it contains no sync package or sync tests, no sync image, no sync CronJob template or values, no sync configuration variables and no in-repository sync operator guide.
- Given the shared Keycloak client used by the agent tool, then it behaves as on the main branch again, with its tests; the only consumer is the agent tool.
- Given the operator documentation, when an operator sets up IdP-managed users, then it covers the switch, how to create the Keycloak service-account client (confidential client with service accounts, the `service_account` user type claim), how to find its platform identity and configure it, the order of first dry run and first live sync before turning the switch on, and points to the customer-side job for scheduling.
- Given the switch is on, then in-platform mutations are rejected, reads and budget management work, and the switch is visible in the public configuration, as before (regression).
- Given the Helm chart, when it is rendered with default values, then it renders as on the main branch plus the switch variable, with no CronJob and no sync-only values (negative).

## Out of Scope
- The management API and identity guard (sibling sub-task).
- The sync job itself (sibling sub-task).
- Removing Keycloak admin credentials from the API workload (the startup migration still reads them); making the switch editable at runtime.

## Estimate
Complexity assessment: M (17/36), about 2–3 days. Mostly deletion with one shared-contract revert (agent-tool Keycloak client) and a documentation rewrite; low risk, wide file touch.

---

## 2. Codebase Findings

### Existing Implementations

Python/Poetry project (pyproject.toml), FastAPI, Helm chart in `deploy-templates/`. `git diff main...HEAD` is ~1355 files (branch 16895 carries much unrelated work), so "sync-only" must be judged by name, not by diff size.

**Purpose: in-repository sync (to be removed).** All tracked, all new vs main:
- `src/idp_sync_job/` (10 files: `__init__`, `_import_check`, `coordinator`, `entrypoint`, `keycloak_source`, `platform_client`, `reconcile`, `settings`, `state`, `tokens`). Task says "coordinator and entrypoint"; the package is larger (Keycloak source, platform HTTP client, reconcile, tokens).
- `tests/idp_sync_job/` (`__init__`, `keycloak_fake.py`, `platform_fake.py`, `test_coordinator/entrypoint/import_purity/keycloak_source/platform_client/reconcile/settings/tokens`).
- `tests/performance/test_idp_sync_list_load.py` — name says sync but it is a list-load test; check what it exercises (likely the management API `idp_sync_service`) before deleting.
- `Dockerfile.sync` (copies `src/idp_sync_job/`, runs `_import_check.py`, `CMD python -m idp_sync_job.entrypoint`).
- `deploy-templates/templates/sync-cronjob.yaml` (86 lines; env `KEYCLOAK_ADMIN_*`, `IDP_SYNC_API_URL/CLIENT_ID/CLIENT_SECRET`).
- `deploy-templates/values.yaml` ~lines 246-300+: top-level `keycloak:` block (`adminUrl`, `adminRealm`; comment "used by the sync job") and `keycloakSync:` block (enabled, schedule, dryRun, batchSize, maxPages, credentialsSecret, idpSync.*, deactivate.*, adminRule.*, image.*, resources, history limits, TTL...). `main` has no `keycloak`/`keycloakSync` values.
- `deploy-templates/README.md` lines ~151-163: generated helm-docs rows for `keycloakSync.*` (and `keycloak.*`); generated from `README.md.gotmpl` + values comments.
- `docs/operations/idp-managed-sync.md` (320 lines; sections: how a run works, build and schedule, the two Keycloak clients, reaching the management API, settings, deactivation, dry run, first live sync, exit codes/log keys, rollback, safeguards, reactivation/budgets, maintainers/pictures, troubleshooting). The only file in `docs/operations/`. No other tracked file links to it except `deploy-templates/values.yaml` and `deploy-templates/README.md`.
- "Four sync configuration variables": no sync variable exists in `src/codemie/configs/config.py`. Sync env vars are read only in `src/idp_sync_job/settings.py` (`SYNC_*`, `IDP_SYNC_API_URL/CLIENT_ID/CLIENT_SECRET`, `KEYCLOAK_ADMIN_*` aliasing) and the CronJob. Which four the ticket means is unverified.
- CI: `.gitlab-ci.yml`, `Makefile`, `pyproject.toml`, `sonar-project.properties`, `.dockerignore` contain no reference to `idp_sync_job` or `Dockerfile.sync` (grep). Branch CI additions are alembic/ruff/poetry checks unrelated to sync.
- Docs under `docs/superpowers/tasks/2026-11-10-rework-sync-job-to-use-idp-sync-api/` are prior run artifacts mentioning the sync; not product docs.

**Purpose: keep (switch, guard, flag, group parsing).**
- `IDP_MANAGED_ENABLED` — `src/codemie/configs/config.py:307`; `_apply_idp_managed_mode` (~1228) turns on `ENABLE_USER_MANAGEMENT`, requires `IDP_PROVIDER=keycloak`. Helm env `IDP_MANAGED_ENABLED` "false" at `deploy-templates/values.yaml:202` (new vs main; this is the "plus the switch variable").
- Consumers: `src/codemie/rest_api/routers/common.py:38` (public config), `src/codemie/rest_api/security/idp_lock.py` (403 guard), `user_management_service.py`, `user_access_service.py`, `authentication_service.py`, `enterprise/idp/dependencies.py`.
- Tests of these: `tests/codemie/rest_api/test_idp_lock_requests.py`, `tests/codemie/service/user/test_idp_managed_service_guards.py`, `test_user_management_service_idp_admin_guard.py`, `test_user_management_router_idp_guard.py`, `routers/test_projects_router_idp_guard.py`, `routers/test_common_router.py`, `tests/codemie/configs/test_config.py`, `tests/enterprise/idp/test_enterprise_idp_wrapper.py`.

**Management API (sibling, out of scope; present on branch):** `src/codemie/rest_api/{models,routers}/idp_sync.py`, `security/idp_sync_guard.py`, `service/user/idp_sync_service.py`, migration `b5c6d7e8f901_add_idp_user_snapshots.py`, `IDP_SYNC_SERVICE_ACCOUNT_ID` (`config.py:280`), tests `*idp_sync*`. Their names contain "idp_sync" but they are NOT sync-job-only; a name-based delete would break the sibling.

**Shared Keycloak client.** `src/codemie_tools/access_management/keycloak/keycloak_client.py` (139 lines): `_get_admin_token`, `_request`, `execute_request(method, relative_url, params)` sending params as JSON body, `health_check`. Only importer: `keycloak/tools.py` (via `access_management/toolkit.py`). Tests present: `tests/codemie_tools/access_management/keycloak/test_models.py` only.

### Decisions Already in the Code
- Keycloak admin credentials (`KEYCLOAK_ADMIN_URL/REALM/CLIENT_ID/CLIENT_SECRET`, `config.py:310-313`) stay: used by startup migration (`KEYCLOAK_MIGRATION_*`; tests `tests/codemie/rest_api/test_main_keycloak_migration.py`, `tests/enterprise/migration/test_coordinator.py`). Out of scope per ticket.
- Management API is gated by a single service-account ID, empty = closed for everyone (`idp_sync_guard.py:44,88`; recorded in `config.py:279`).
- Chart tests live in `deploy-templates/tests/` (helm-unittest: `merge_env_lists_test.yaml`, `metrics_test.yaml`); none mention sync or keycloak.

### Task Statements Checked Against the Code
- "Branch rewrote the shared Keycloak client (token cache, 401 retry, query params, parsed JSON)": **contradicted in this checkout**. `git diff main HEAD` and `git log main..HEAD` on `src/codemie_tools/access_management` and its tests are empty; the client has no token cache, 401 retry or query params and sends `params` as JSON body, i.e. it is already as on main. The rewrite may live only on a different branch/commit (e.g. remote 16895 tip vs local); verify before planning a revert.
- "Nothing else in the repository references the sync": **holds** for CI/Makefile/pyproject/Dockerfile; references exist only in chart values, chart README and the operator doc (all sync-only).
- "Four sync configuration variables": not found in `config.py`; see above (unverified).
- "Sync image not built by CI": **holds** (no reference in `.gitlab-ci.yml`).
- "Helm default render = main plus switch variable": `values.yaml` also gained other branch additions (hosted-apps templates, clamd, `_helpers.tpl` +62 lines, extra values ~322 lines total), so equality with main after removal depends on 16895 content beyond sync; keeps non-sync diff in place.
- "Sync is the only other consumer of the client": **holds** (grep: only `tools.py`; `keycloak_source.py` has its own code).

### Consumers of Changed Contracts
- `KeycloakClient`: only `access_management/keycloak/tools.py`. Not changed on this checkout.
- Removed values `keycloak.*` / `keycloakSync.*`: referenced by `sync-cronjob.yaml` and `deploy-templates/README.md` only.
- Removed `idp_sync_job`: referenced by `Dockerfile.sync` and its tests only; `test_import_purity.py` inspects the package.

### Architecture and Layers Affected
Deployment/Helm (values, template, generated README), container build (Dockerfile.sync), standalone package `src/idp_sync_job`, tests, operator documentation. Application layers (config, security, service) are left untouched.

### Integration Points
CronJob -> Keycloak admin API and -> platform `/v1/idp-sync/` (both disappear). Retained: Keycloak provider, `service_account` identity checks in `idp_sync_guard`.

### Patterns and Conventions
Chart README generated with helm-docs from `README.md.gotmpl` (values comments with `# --`). Docs in `docs/operations/`. Guides index `.ai-run/guides/` (see `integration/` for Keycloak-related docs).

---

## 3. Documentation Findings

### Guides and Architecture Docs
`.ai-run/guides/` exists (AGENTS.md index; `development/configuration-patterns.md`, `quality-gates.md`). No guide for IdP-managed mode; the only operator doc is `docs/operations/idp-managed-sync.md`, which sections "The two Keycloak clients", "First live sync", "Running a dry run first", "Rollback", "Reactivation and budgets" overlap the new doc's required content.

### Architectural Decisions
Recorded in `config.py` comments (switch semantics, sync service account) and prior task artifacts under `docs/superpowers/tasks/2026-11-10-...`.

### Derived Conventions
Docs are standalone markdown with English imperative steps; chart documentation is regenerated, not hand-edited.

---

## 4. Testing Landscape

### Existing Coverage
Sync tests listed in Section 2. Switch/guard tests listed in Section 2. Keycloak tool: only `test_models.py` (no client tests).

### Testing Framework and Patterns
pytest (pytest.ini), fixtures/fakes (`keycloak_fake.py`, `platform_fake.py` are used only by `tests/idp_sync_job`). helm-unittest for chart.

### Coverage Gaps
No tests for `KeycloakClient`; AC says "with its tests", so tests for the main-branch client do not exist today. No chart test asserting no CronJob renders. OpenAPI baseline `tests/codemie/rest_api/router_guards/baselines/openapi.json` mentions idp-sync (management API; unaffected).

---

## 5. Configuration and Environment

### Environment Variables
Keep: `IDP_MANAGED_ENABLED`, `IDP_SYNC_SERVICE_ACCOUNT_ID`, `KEYCLOAK_ADMIN_*`, `KEYCLOAK_MIGRATION_*`. Sync-job-only (read in `src/idp_sync_job/settings.py`, set in CronJob): `SYNC_ENABLED`, `SYNC_DRY_RUN`, `SYNC_BATCH_SIZE`, `SYNC_MAX_PAGES`, `SYNC_DEACTIVATE*`, `SYNC_ADMIN_ROLE_NAME`, `SYNC_ADMIN_USER_ID`, `IDP_SYNC_API_URL`, `IDP_SYNC_CLIENT_ID`, `IDP_SYNC_CLIENT_SECRET`.

### Configuration Files
`deploy-templates/values.yaml`, `deploy-templates/README.md`. `docker-compose*.yml`, `standalone/` have no sync settings.

### Feature Flags and Deployment Concerns
`IDP_MANAGED_ENABLED` env in chart default "false". Secrets referenced by CronJob (`codemie-keycloak-sync-read`, `codemie-idp-sync-write`) are cluster-side, not in repo.

---

## 6. Risk Indicators

- Speculative: name-based deletion of `*idp_sync*` would also remove the management API (sibling sub-task); `src/idp_sync_job` and `tests/idp_sync_job` are the safe boundary.
- `tests/performance/test_idp_sync_list_load.py` ownership unclear (sync job vs management API).
- Ticket's Keycloak client rewrite not present on this checkout; revert may be a no-op or be on another ref (Section 2 statements).
- "Four sync config variables" not locatable in `config.py`.
- Helm "renders as main plus switch" cannot be met by sync removal alone: values/_helpers/hosted-apps differences from 16895 remain; need a rendered-diff baseline decision.
- `deploy-templates/README.md` is generated; manual edit vs helm-docs regeneration.
- No tests for `KeycloakClient`.
- Docs: new operator doc must cover service-account client, `service_account` claim, identity lookup; the claim check lives in `idp_sync_guard.py` (sibling) so content depends on it.

---

## 7. Summary for Complexity Assessment

Changes touch mostly deletions: `src/idp_sync_job` (10 files), `tests/idp_sync_job` (11 files), possibly one performance test, `Dockerfile.sync`, `sync-cronjob.yaml`, two values blocks, generated chart README rows, and one 320-line operator guide to be rewritten. No application-layer code changes appear necessary; switch, guard, config flag and tests remain.

Technical novelty is low. The ticket's one "shared-contract revert" (Keycloak client) does not match this checkout: the client and its tool are already identical to main, and there are no client tests. The "four config variables" are not in `config.py`; sync env vars exist only inside the job package and CronJob.

Risks are boundary mistakes (management API shares the `idp_sync` name), unverifiable Helm "as main" equivalence given large unrelated branch differences, and documentation correctness against the sibling's guard behaviour. Existing tests for the retained switch behaviour are broad.

---

## 8. External References

None named by the task (branches and Jira URL only; the Jira page was not fetched). Branch comparison used local `main`; branch EPMCDME-16895 exists locally.
