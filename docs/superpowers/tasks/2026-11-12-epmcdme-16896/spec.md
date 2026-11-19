# EPMCDME-16896: Remove the in-repository Keycloak sync

## Goal
The branch already holds the management API (EPMCDME-16895) and the sync job (EPMCDME-16897). This task removes the job and everything that existed only for it, after exporting it for the customer's repo, and replaces its operator guide with one built around the service-account model. The job moves to the customer's repo as a client of the management API (sibling sub-task). Branch is cut from EPMCDME-16895 (tip `a72ddf13`, equal to `origin/EPMCDME-16895`); the MR targets it.

## Changes
1. **Export first (before any deletion).** Write a self-contained export of the job to `docs/superpowers/tasks/2026-11-12-epmcdme-16896/transfer/`, taken from tip `a72ddf13`: `src/idp_sync_job/`, `tests/idp_sync_job/`, `Dockerfile.sync`, `deploy-templates/templates/sync-cronjob.yaml`, the `keycloak:` and `keycloakSync:` values blocks, and `docs/operations/idp-managed-sync.md` as source material. Form: a `git format-patch` series or a copied tree. Add a short `README.md` there on how to apply or adapt it. Nothing from `transfer/` goes into the repository source tree; the export is not wired into any build.
2. Delete `src/idp_sync_job/` (10 files), `tests/idp_sync_job/` (incl. `keycloak_fake.py`, `platform_fake.py`), `Dockerfile.sync`, `deploy-templates/templates/sync-cronjob.yaml`.
3. `deploy-templates/values.yaml`: remove the top-level `keycloak:` block (used only by the CronJob) and the whole `keycloakSync:` block. Keep `IDP_MANAGED_ENABLED` (values.yaml:202).
4. `deploy-templates/README.md`: drop the `keycloak.*` / `keycloakSync.*` rows. Regenerate with helm-docs; if unavailable, remove exactly those rows.
5. Delete `docs/operations/idp-managed-sync.md`. Add `docs/operations/idp-managed-users.md` (about 100 lines) covering:
   - the `IDP_MANAGED_ENABLED` switch: requires `IDP_PROVIDER=keycloak`, enables user management, mutations return 403, reads and budgets work, shown in public config, set at deploy time only;
   - creating the Keycloak client: confidential client, service accounts enabled, mapper so the token carries the `service_account` user type claim;
   - finding the client's platform identity and setting it as `IDP_SYNC_SERVICE_ACCOUNT_ID` (empty = management API closed to everyone);
   - order: first dry run, then first live sync, then switch on;
   - scheduling lives in the customer-side job; this repo ships no scheduler.
   It describes guard behaviour (from `idp_sync_guard.py`) without line numbers and links no removed file.
6. Shared Keycloak client `src/codemie_tools/access_management/keycloak/keycloak_client.py`: no code change. 16897 already returned the tool to the main version. Add `tests/codemie_tools/access_management/keycloak/test_keycloak_client.py`, a characterization of current main behaviour (admin token fetch, `params` sent as JSON body on GET-style calls, `health_check`), HTTP mocked.
7. Add a helm-unittest case in `deploy-templates/tests/` asserting no `CronJob` renders with default values.

## Acceptance criteria
- **Removed:** no `src/idp_sync_job`, `tests/idp_sync_job`, `Dockerfile.sync`, `sync-cronjob.yaml`, `idp-managed-sync.md`, no `keycloak:` / `keycloakSync:` values. Grep clean outside `docs/superpowers/` for `idp_sync_job`, `Dockerfile.sync`, `keycloakSync`, `idp-managed-sync` and for these variables: `SYNC_ENABLED`, `SYNC_DRY_RUN`, `SYNC_BATCH_SIZE`, `SYNC_MAX_PAGES`, `SYNC_DEACTIVATE`, `SYNC_DEACTIVATE_MAX_RATIO`, `SYNC_DEACTIVATE_MIN_USERS`, `SYNC_ADMIN_ROLE_NAME`, `SYNC_ADMIN_USER_ID`, `IDP_SYNC_API_URL`, `IDP_SYNC_CLIENT_ID`, `IDP_SYNC_CLIENT_SECRET`. Names that stay and are not hits: `PLATFORM_DATASOURCES_SYNC_ENABLED`, `IDP_SYNC_SERVICE_ACCOUNT_ID`, `KEYCLOAK_ADMIN_*` (in `config.py`, used by the startup migration).
- **Export:** `transfer/` exists under the task dir with the items in Change 1 and a README, was made from `a72ddf13` before deletion, and nothing from it is tracked outside `docs/superpowers/`.
- **Client:** `keycloak_client.py` and `tools.py` have an empty diff against `main` and against `origin/EPMCDME-16895`; the new tests pass; the only importer is `keycloak/tools.py`.
- **Doc:** covers all points in Change 5.
- **Regression:** existing switch tests (analysis section 2) pass unchanged: mutations rejected with the switch on, reads and budgets work, switch in public config.
- **Helm:** the baseline is the default `helm template` render at the 16895 tip, not "main plus the switch". The render after this task differs from it exactly by the removed CronJob and the removed `keycloak`/`keycloakSync` values. `IDP_MANAGED_ENABLED` is still present and no `CronJob` kind renders. Differences from `main` (hosted-apps, clamd, helpers, the switch variable) are 16895 content and are not changed here.

## Non-goals
- Management API, its guard, snapshots migration, `IDP_SYNC_SERVICE_ACCOUNT_ID` and all `*idp_sync*` files outside `src/idp_sync_job` and `tests/idp_sync_job`.
- Building the customer-side job; the export is source material only.
- Removing `KEYCLOAK_ADMIN_*` from the API workload.
- Making the switch editable at runtime.
- Removing non-sync 16895 additions (hosted-apps templates, clamd, helpers), or making the chart equal to `main`.

## Decisions
- Include a transfer export saved only under the task dir over deleting without a hand-off or only documenting commit ranges — user
- Helm baseline is the 16895 tip default render minus sync-only content over "main plus the switch" (this task removes 16897's job; 16895's other chart changes are not its scope).
- Client "revert" is a no-op with characterization tests over a code change, because 16897 already returned it to main and it is verified against `origin/EPMCDME-16895`.
- Add small tests for the client over none, because the criterion says "with its tests" and none exist.
- Delete by package boundary over matching `*idp_sync*`, because management-API files share the name.
- Keep `tests/performance/test_idp_sync_list_load.py`: it exercises `IdpSyncService` (management API).
- The "four sync config variables" are the job's own env vars (exact list in the AC) over searching `config.py`, where none exist (16897 already dropped `KEYCLOAK_SYNC_*`).
- Exact variable list in the grep criterion over a `SYNC_*` pattern, which also matches `PLATFORM_DATASOURCES_SYNC_ENABLED`.
- Regenerate chart README with helm-docs over hand edit, with row removal as fallback.
- New doc `idp-managed-users.md` over editing the old guide in place.
- Add a helm-unittest "no CronJob" case over manual render checks.

## Risks
- Doc accuracy depends on the sibling's guard; re-check when it merges.
- The Helm diff against the tip and helm-docs availability were not run during specification; the implementer checks both.
- `PLATFORM_DATASOURCES_SYNC_ENABLED` and similar names must survive deletions.

## Scope update 2026-11-17

`EPMCDME-16895` was rebuilt on a newer `main` and the job became one self-contained folder, `idp-sync-job/`
(own Dockerfile, chart, tests, README). The platform repository no longer has `src/idp_sync_job`,
`tests/idp_sync_job`, `Dockerfile.sync`, `deploy-templates/templates/sync-cronjob.yaml` or the `keycloak:` and
`keycloakSync:` values. The task therefore reduces to:

- delete `idp-sync-job/` and `docs/operations/idp-managed-sync.md`;
- drop the two `idp-sync-job/tests` lines from `pyproject.toml` and `scripts/check_ruff_changed.py`;
- keep `docs/operations/idp-managed-users.md` (service-account model, now on `IDP_SYNC_SERVICE_ACCOUNT_USERNAME`)
  and the Keycloak client characterization tests;
- the chart needs no change beyond `IDP_MANAGED_ENABLED`, so the helm-unittest case is dropped.

The earlier chart and `src` deletions in this spec are superseded.
