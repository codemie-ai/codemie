# EPMCDME-16897 Keycloak sync job via /v1/idp-sync/ Implementation Plan

**Goal:** the sync job reconciles Keycloak with the platform only through `/v1/idp-sync/`, with no `codemie*` import and no DB. Spec: `spec.md`; research: `technical-analysis.md`.

**Conventions:** commit per task using the repository's existing convention (follow `git log`, no AI footer). Apache header on new files. Tests: pytest + pytest-asyncio, `httpx.MockTransport` for the platform and Keycloak. Guides: `.ai-run/guides/testing/testing-patterns.md`, `standards/code-quality.md`, `development/configuration-patterns.md`.

**Layout decision (spec fallback, applied up front):** `src/codemie/enterprise/__init__.py:19-113` imports `.loader`, `.langfuse`, `.litellm`, `.idp` etc., so any module under `codemie.enterprise.sync` drags platform code in and the blocked-import check cannot pass. The job therefore becomes the top-level package `src/idp_sync_job/` (approved fallback). Modules: `settings.py`, `tokens.py` (TokenSource), `keycloak_source.py` (read only), `platform_client.py`, `state.py` (single `UserState` type), `reconcile.py` (pure diff), `coordinator.py` (run/apply/status), `entrypoint.py`.

### Task 1: Relocate the package
- `git mv src/codemie/enterprise/sync src/idp_sync_job` and `tests/codemie/enterprise/sync tests/idp_sync_job`; fix test imports only. Add `src/idp_sync_job` to the pytest/ruff paths if the root config lists packages explicitly (`pyproject.toml`).
- Test-first: no — behavior unchanged; existing moved tests must still pass.

### Task 2: Job-local settings, TokenSource, import-purity test
- New `settings.py`: frozen dataclass read from env: `SYNC_ENABLED`, `SYNC_DRY_RUN`, `SYNC_BATCH_SIZE`, `SYNC_MAX_PAGES` (2000), `SYNC_DEACTIVATE`, `SYNC_DEACTIVATE_MAX_RATIO` (0.10), `SYNC_DEACTIVATE_MIN_USERS`, `SYNC_ADMIN_ROLE_NAME`, `KEYCLOAK_ADMIN_{URL,REALM,CLIENT_ID,CLIENT_SECRET}`, `IDP_SYNC_{API_URL,CLIENT_ID,CLIENT_SECRET}`; `missing()` lists unset required names.
- New `tokens.py`: ONE `TokenSource(token_url, client_id, secret, client)` used by both the Keycloak source and the platform client: client_credentials, cached with a safety margin (refresh before `expires_in`), explicit connect/read timeouts, `invalidate()` for the single re-auth.
- New `tests/idp_sync_job/test_import_purity.py`: subprocess with `-I`, a `sys.meta_path` finder that raises on `codemie` / `codemie_tools` (and `sqlalchemy`, `asyncpg`, `langchain_core`), `PYTHONPATH` = `src/idp_sync_job`'s parent only; imports every module in a list. Later tasks append their module to the list.
- Test-first: yes — purity test fails while `coordinator.py` still imports `codemie.configs`; TokenSource test: second call within validity makes no token request, call past the margin re-requests.

### Task 3: Keycloak source (read half)
- New `keycloak_source.py`: local httpx client (no `KeycloakClient`, not edited) built on TokenSource, one 401 retry. Port from `coordinator.py` (`_fetch_users`, `_get_all`, `_fetch_admin_user_ids`, `_subgroups`, `_fetch_group_assignments`, `_kc_profile`, `_is_admin`) with admin role/id injected from settings. Page ceiling = `SYNC_MAX_PAGES`; hitting it or any read error marks the snapshot `complete=False` (partial reads still yield None for admins/groups as today). Typed `KeycloakUnavailable(reason)` with reasons `unreachable | read_error | no_users | not_configured`. Legacy `applications*` attributes kept.
- Reuse and move `_fakes.py::FakeKeycloak` and `test_coordinator_keycloak_reads.py` (rename `test_keycloak_source.py`); adapt to the new module.
- Test-first: yes — truncation at `SYNC_MAX_PAGES` returns `complete=False`; Keycloak 5xx on `/users` raises `KeycloakUnavailable("read_error")`; a timeout maps to `unreachable`.

### Task 4: Platform API client
- New `platform_client.py` (httpx + TokenSource with the separate `IDP_SYNC_*` credentials): `list_users()` pages `GET /v1/idp-sync/users?per_page=100` until `total` is reached and returns every user (deactivated included) only after the last page; `upsert_user`, `put_membership`, `delete_membership`, `deactivate`. Errors: `ApiUnreachable` (connect/timeout/5xx), `AccountRefused` (302/401/403 repeated after ONE `invalidate()` + retry), `PermanentRefusal` (409 and similar known permanent refusals, carries detail), `UserFailure` (other 4xx).
- Test-first: yes — MockTransport 401 then 200 succeeds after one re-auth; 401 twice raises `AccountRefused`; 409 raises `PermanentRefusal`; connect timeout raises `ApiUnreachable`.

### Task 5: UserState and pure diff
- `state.py`: one `UserState` dataclass (id, email, username, name, is_admin, active, memberships: dict[project, is_project_admin], personal projects flagged) built from both `KeycloakUser` and `IdpUserResponse`; used by the diff and to build `IdpUserUpsertRequest`. `reconcile.py` (no I/O) returns a `Plan` of ordered actions: upsert (create/update/reactivate), add/remove membership, admin change.
- Rules: personal memberships never added/removed; memberships reconciled only when Keycloak names at least one project; no email -> skip user; admin unchanged when the role read failed; equal state -> empty plan; admin promotions ordered before demotions.
- Email-collision pre-detection: `GET /users` returns `email` per `IdpUserResponse` (`src/codemie/rest_api/models/idp_sync.py:65-72`), so index the full platform snapshot by lowercase email (deactivated included); a Keycloak user whose email is owned by a different id becomes a `skipped_email_collision` entry (warning, counted under `failures`-free `skipped_users`), no write.
- Test-first: yes — identical states give zero actions; collision case yields a skip not an upsert; promotion precedes demotion in the action order; empty Keycloak memberships remove nothing.

### Task 6: Deactivation planning
- In `reconcile.py`: target = active platform users absent from the full Keycloak snapshot, only when `SYNC_DEACTIVATE` is on. Return no targets when the snapshot is incomplete, has 0 users, or has fewer than `SYNC_DEACTIVATE_MIN_USERS`. Ratio over `SYNC_DEACTIVATE_MAX_RATIO` of active platform users raises `DeactivationCeilingExceeded` before any action is emitted.
- Test-first: yes — flag off gives no targets; truncated snapshot gives none; ratio 0.11 raises; target set equals active-and-absent users.

### Task 7: Coordinator (apply, statuses, logging)
- Rewrite `coordinator.py`: not enabled/not configured/`KeycloakUnavailable` -> `skipped`, no write, a distinct warning key per reason (`sync_skipped_not_enabled`, `sync_skipped_keycloak_unreachable`, `sync_skipped_keycloak_read_error`, `sync_skipped_keycloak_no_users`, `sync_skipped_not_configured`). Read both snapshots fully, then apply the plan; per-user try/except: `PermanentRefusal` -> `refused` (logged per user, exit 0), `UserFailure` -> `failures`; `ApiUnreachable`/`AccountRefused`/ceiling exceeded stop the run -> `failed`. Dry run logs `dry_run:` per change, makes no write call. Final line `sync_run_complete: status=... created, updated, reactivated, deactivated, memberships_added, memberships_removed, admin_changes, refused, failures`. Remove `CodemieSyncDeps`, savepoints, `leader_lock`, `UserDB`.
- Replace the DB tests (users, memberships, personal projects, dry run, lifecycle) by MockTransport tests against a fake platform API; delete the lock/DB/savepoint fakes.
- Test-first: yes — equal state records zero non-GET requests; Keycloak 0 users gives `skipped` and no requests to the platform; API 403 twice gives `failed`; one user 500 leaves others reconciled and `failed`; 409 last-admin gives `refused` and ok; dry run issues only GETs; counters appear in `sync_run_complete`.

### Task 8: Entrypoint
- Rewrite `entrypoint.py`: build settings, validate env (missing -> `skipped` with `sync_skipped_not_configured`, exit 0), run the coordinator with `asyncio.run`, exit 1 only for `failed`. No engine, no repositories.
- Test-first: yes — skipped and ok exit 0, failed exits 1, missing `IDP_SYNC_*` env exits 0 with the skip key; replace `test_entrypoint.py` DB cases.

### Task 9: Slim Dockerfile.sync
- Rewrite `Dockerfile.sync` (:1-90): install only `httpx` (pinned as in `pyproject.toml:112`), copy only `src/idp_sync_job`, no `config/`, no platform packages, `CMD python -m idp_sync_job.entrypoint`; build step runs the same blocked-import check as Task 2 with only job files present.
- Test-first: no — packaging; verified by the image build in the calling flow.

### Task 10: CronJob and values
- `deploy-templates/templates/sync-cronjob.yaml`: `concurrencyPolicy: Forbid`, `backoffLimit: 0`, `restartPolicy: Never`, `activeDeadlineSeconds` default 1800; drop `POSTGRES_*` and the customer ConfigMap mount; add env for all job settings; admin rule env both fed from one `keycloakSync.adminRule.*` values source.
- `deploy-templates/values.yaml:203-249`: remove `database.*` and unused keys; add `idpSync.apiUrl`, `deactivate.*`, `maxPages`, `adminRule.*`. The one client gets its own default Secret (`keycloakSync.credentialsSecret`, not `oauth2-proxy`). Helm `fail` in the template when `keycloakSync.database` is set, with a message pointing to the docs.
- Test-first: no — no Helm test harness in the repo.

### Task 11: Remove unused settings (after consumer grep)
- First `Grep` for `KEYCLOAK_SYNC_` across `src`, `tests`, `deploy-templates`, `docs`, `standalone`; proceed only when the sole hits are the removed job code, its tests and the CronJob. Then delete `KEYCLOAK_SYNC_ENABLED/SCHEDULE/BATCH_SIZE/DRY_RUN` from `src/codemie/configs/config.py:277-281`. `KEYCLOAK_ADMIN_*`, `IDP_SYNC_SERVICE_ACCOUNT_ID`, `IDP_MANAGED_ENABLED` stay untouched.
- Test-first: no — removal; the existing config tests must still pass.

### Task 12: Operator documentation
- Rewrite `docs/operations/idp-managed-sync.md`: build and schedule; the one Keycloak client (roles `view-users, query-users, query-groups, view-realm`; required claims `sub`, `preferred_username` = `IDP_SYNC_SERVICE_ACCOUNT_USERNAME`, `name` via hardcoded-claim mapper, `realm_access.roles`, `user_type=service_account`, optional `email`); table of all job env variables; status/exit-code table identical to the spec; proxy checklist (4 points of the spec); dry run, first live sync (raise `activeDeadlineSeconds` once for 60,000 users), rollback by suspending the CronJob; breaking Helm key changes; skip log keys to alert on; lost safeguards (maintainers, picture).
- Test-first: no — documentation.

### Self-check
- plan_notes: both snapshots read fully, promotions before demotions (Task 5/7); one TokenSource (Task 2/3/4); own read Secret (Task 10); distinct skip keys (Task 7); Helm `fail` on `database.*` (Task 10); single `UserState`, Keycloak source apart from reconcile (Task 3/5).
- negative-constraints: platform API/guard/`IDP_SYNC_SERVICE_ACCOUNT_USERNAME`/`IDP_MANAGED_ENABLED`/mode locks unchanged (no task touches them; Task 11 touches only `KEYCLOAK_SYNC_*`); no DB or lock (Tasks 7, 8, 9); `KeycloakClient` and `enterprise/migration` not edited (Task 3 is a local copy); no new CI or repo; no deactivation unless the flag is on (Task 6); no platform change for maintainers or pictures (Task 12 documents).
