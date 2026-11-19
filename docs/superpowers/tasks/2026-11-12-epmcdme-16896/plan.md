# Remove the in-repository Keycloak sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Export, then delete the in-repo Keycloak sync job and its chart/image/docs, add client characterization tests and a no-CronJob chart test, and replace the operator guide.

**Architecture:** Delete by package boundary (`src/idp_sync_job`, `tests/idp_sync_job`), never by `*idp_sync*` name (management API files share it). No application-layer code changes. Commit per task using the repository's existing convention (follow `git log`; no AI footer).

**Tech Stack:** Python/pytest, Helm + helm-unittest, helm-docs, Markdown.

**Spec:** `docs/superpowers/tasks/2026-11-12-epmcdme-16896/spec.md`

## Global Constraints

- Export source is tip `a72ddf13` (equal to `origin/EPMCDME-16895`), made BEFORE any deletion; MR targets EPMCDME-16895.
- Keep: `IDP_MANAGED_ENABLED` (values.yaml:202), `IDP_SYNC_SERVICE_ACCOUNT_ID`, `KEYCLOAK_ADMIN_*`, `PLATFORM_DATASOURCES_SYNC_ENABLED`, all management-API `*idp_sync*` files, `tests/performance/test_idp_sync_list_load.py`.
- Nothing from `transfer/` is tracked outside `docs/superpowers/`; it is wired into no build.
- No whole-suite gates, browser checks, or review tasks here (later stages own them). Commands are named; do not assume exact outputs.

## Review Focus

- A broad delete removing management-API `*idp_sync*` files: Task 2 greps that they still exist.
- Removing `SYNC_*`-like names that also hit `PLATFORM_DATASOURCES_SYNC_ENABLED`: Task 2 greps with the exact variable list.
- `keycloak:` values removal breaking other templates: Task 3 renders before/after.
- Characterization test pinning JSON body on GET-style calls must be named as characterization of current main behaviour so a later query-param fix changes it knowingly.

---

### Task 1: Export the job to `transfer/`

**Files:**
- Create: `docs/superpowers/tasks/2026-11-12-epmcdme-16896/transfer/` (copied tree + `README.md`)

Test-first: no — file export, no behaviour to test.

- [ ] Using `git show`/`git archive a72ddf13 -- <paths>` (not the working tree), copy into `transfer/`: `src/idp_sync_job/`, `tests/idp_sync_job/`, `Dockerfile.sync`, `deploy-templates/templates/sync-cronjob.yaml`, `docs/operations/idp-managed-sync.md` (as source material), and the `keycloak:` + `keycloakSync:` blocks of `deploy-templates/values.yaml` saved as `transfer/values-snippet.yaml`. If git is unusable, copy the working tree files and confirm with `git diff a72ddf13 -- <paths>` that they are unchanged. Add `transfer/README.md` (short): what is included, source commit, how to apply (copy tree into the customer repo, rewire env vars to the management API client credentials) and that it is source material only. Commit.

### Task 2: Delete the sync package, tests, image, CronJob

**Files:**
- Delete: `src/idp_sync_job/`, `tests/idp_sync_job/`, `Dockerfile.sync`, `deploy-templates/templates/sync-cronjob.yaml`

Test-first: no — pure deletion; verified by grep and imports.

- [ ] Delete the four paths via `git rm -r`. Do not touch any other `*idp_sync*` file.
- [ ] Verify management API remains: `ls src/codemie/rest_api/routers/idp_sync.py src/codemie/rest_api/security/idp_sync_guard.py src/codemie/service/user/idp_sync_service.py tests/performance/test_idp_sync_list_load.py`.
- [ ] Run `pytest --collect-only -q tests/codemie/rest_api tests/codemie/service/user` (collection must not error on missing modules).
- [ ] Commit.

### Task 3: Remove sync values and chart README rows

**Files:**
- Modify: `deploy-templates/values.yaml` (top-level `keycloak:` block, ~246-300+, and the whole `keycloakSync:` block; keep line ~202 `IDP_MANAGED_ENABLED`)
- Modify: `deploy-templates/README.md` (generated)

Test-first: no — config removal; verified by render diff.

- [ ] Before editing, save `helm template` default render of the chart to a temp file outside the repo (baseline; already minus the deleted CronJob template, so the diff is values-only; also render from `a72ddf13` if feasible to confirm the only difference is the CronJob).
- [ ] Grep `deploy-templates/` for `.Values.keycloak` to confirm nothing but the deleted CronJob read it.
- [ ] Remove both blocks (and their comments) from `values.yaml`.
- [ ] Regenerate `deploy-templates/README.md` with helm-docs; if unavailable, delete exactly the `keycloak.*` / `keycloakSync.*` rows.
- [ ] Re-render and diff against the baseline: expected no difference other than the CronJob (absent) and `IDP_MANAGED_ENABLED` still present. Commit.

### Task 4: Helm test asserting no CronJob by default

**Files:**
- Create: `deploy-templates/tests/no_cronjob_test.yaml` (follow the shape of `deploy-templates/tests/metrics_test.yaml`)

Test-first: yes — a test with default values asserting `notContains`/`isKind` that no `CronJob` document renders across the chart's templates (fails on `a72ddf13` where `sync-cronjob.yaml` could render; passing after Task 2 is the intended post-state).

- [ ] Write the test: default values, no template of kind `CronJob` rendered (use the `templates:` list of all templates or per-document `isNotKind` as helm-unittest supports; mirror existing tests).
- [ ] Run `helm unittest deploy-templates`; the new suite must pass. Commit.

### Task 5: Characterization tests for `KeycloakClient`

**Files:**
- Create: `tests/codemie_tools/access_management/keycloak/test_keycloak_client.py`
- No change to `src/codemie_tools/access_management/keycloak/keycloak_client.py` or `tools.py`.

Test-first: no — characterization of existing behaviour; tests pass on first run (no failing-test phase applies).

- [ ] Verify `git diff main -- src/codemie_tools/access_management` and `git diff origin/EPMCDME-16895 -- src/codemie_tools/access_management` are empty; grep that `keycloak_client` is imported only by `keycloak/tools.py`. If the diff is not empty, stop and report instead of editing.
- [ ] Add tests, httpx mocked (patch `httpx.Client` in the `keycloak_client` module), with a module docstring stating these characterize current main behaviour (including the JSON-body quirk) so a later fix to query params changes them knowingly:
  - `test_get_admin_token_posts_client_credentials`: POSTs to `{base}/realms/{realm}/protocol/openid-connect/token` with `data` containing `client_id`, `client_secret`, `grant_type=client_credentials`; returns `access_token`.
  - `test_get_admin_token_http_error_raises_tool_exception` (message starts `Failed to obtain admin token - HTTP`).
  - `test_execute_request_sends_params_as_json_body_on_get`: `execute_request("GET", "/users", {"max": 10})` calls `client.request(method="GET", url=".../admin/realms/{realm}/users", json={"max": 10}, headers with Bearer token)`; returns response text.
  - `test_execute_request_rejects_relative_url_without_slash` (ToolException).
  - `test_health_check_true_on_token_success` and `test_health_check_wraps_failure_in_tool_exception`.
- [ ] Run `pytest tests/codemie_tools/access_management/keycloak -v`. Commit.

### Task 6: Replace the operator guide

**Files:**
- Delete: `docs/operations/idp-managed-sync.md`
- Create: `docs/operations/idp-managed-users.md` (~100 lines, imperative English steps)

Test-first: no — documentation.

- [ ] Read `src/codemie/rest_api/security/idp_sync_guard.py` and `config.py` (~279-313, `_apply_idp_managed_mode`) for behaviour; describe behaviour only, no line numbers, no links to removed files or to `transfer/`.
- [ ] Write sections: (1) the `IDP_MANAGED_ENABLED` switch: requires `IDP_PROVIDER=keycloak`, enables user management, mutations return 403, reads and budgets work, shown in public configuration, deploy-time only; (2) create the Keycloak client: confidential, service accounts enabled, mapper so the token carries the `service_account` user type claim; (3) find the client's platform identity and set it as `IDP_SYNC_SERVICE_ACCOUNT_ID` (empty = management API closed to everyone); (4) order: first dry run, then first live sync, then switch on; (5) scheduling lives in the customer-side job; this repo ships no scheduler.
- [ ] Remove the old doc and drop any remaining reference to it (`values.yaml`/chart README refs should already be gone after Task 3). Commit.

### Task 7: Final grep sweep

**Files:** none (fix-ups only if hits)

Test-first: no — verification of removal.

- [ ] Run Grep outside `docs/superpowers/` for `idp_sync_job|Dockerfile\.sync|keycloakSync|idp-managed-sync` and for the exact variables: `SYNC_ENABLED`, `SYNC_DRY_RUN`, `SYNC_BATCH_SIZE`, `SYNC_MAX_PAGES`, `SYNC_DEACTIVATE`, `SYNC_DEACTIVATE_MAX_RATIO`, `SYNC_DEACTIVATE_MIN_USERS`, `SYNC_ADMIN_ROLE_NAME`, `SYNC_ADMIN_USER_ID`, `IDP_SYNC_API_URL`, `IDP_SYNC_CLIENT_ID`, `IDP_SYNC_CLIENT_SECRET` (use word-boundary patterns; `PLATFORM_DATASOURCES_SYNC_ENABLED`, `IDP_SYNC_SERVICE_ACCOUNT_ID`, `KEYCLOAK_ADMIN_*` are not hits). Zero hits expected; fix any in a small commit.
- [ ] Run the existing switch regression tests unchanged: `pytest tests/codemie/rest_api/test_idp_lock_requests.py tests/codemie/service/user/test_idp_managed_service_guards.py tests/codemie/service/user/test_user_management_service_idp_admin_guard.py tests/codemie/rest_api/routers/test_common_router.py tests/codemie/configs/test_config.py -q` (adjust paths to actual locations via Glob).

## Negative-constraint pass

- Delete by package boundary only, keep management API / list-load test: Task 2 (verify step).
- No variable renames of `PLATFORM_DATASOURCES_SYNC_ENABLED` etc.: Task 7 word-boundary grep.
- Nothing from `transfer/` outside `docs/superpowers/`; not wired into build: Task 1.
- No client code change: Task 5 guard step.
- Chart not made equal to main; non-sync 16895 additions untouched: Task 3 diff against baseline.
- No `KEYCLOAK_ADMIN_*` removal, no runtime switch editing: untouched by all tasks.
