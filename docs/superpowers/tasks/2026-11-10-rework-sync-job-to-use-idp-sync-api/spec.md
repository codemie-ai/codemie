# EPMCDME-16897: Keycloak sync job reconciles through the /v1/idp-sync/ management API

## Goal

The existing job (`src/codemie/enterprise/sync/`: `coordinator.py`, `entrypoint.py`, `Dockerfile.sync`, `deploy-templates/templates/sync-cronjob.yaml`) stops writing to the platform database and applies Keycloak differences through the management API (`src/codemie/rest_api/routers/idp_sync.py`, `src/codemie/rest_api/models/idp_sync.py`). After testing here the job is handed to the customer, so it must be portable: no `codemie.*` import, no platform database, a slim image. The Keycloak read logic in `coordinator.py` (`_fetch_users`, `_fetch_admin_user_ids`, `_fetch_group_assignments`, `_kc_profile`) is kept; the write path, PG advisory lock, DB session and repository wiring are replaced. The platform is not changed.

## Behavior

- Reads from Keycloak: enabled users with profile, admin realm role, membership and project-admin right from group paths under `projects/` and `project-admins/` (plus legacy `applications*` attributes, as today). Reads platform state via `GET /v1/idp-sync/users` (100 per page, includes deactivated) and diffs against it; the API has no "unchanged" signal.
- Writes only differences: `PUT /users/{id}` (create, update, reactivate, admin flag), `PUT|DELETE /users/{id}/projects/{project}`, `POST /users/{id}/deactivate`. Both snapshots are read fully before any write; admin promotions are applied before demotions.
- Personal projects are never touched. Users absent from or disabled in Keycloak are untouched unless deactivation is on (below).
- Dry run logs every change it would make and performs no write call.

## Deactivation (default off)

- Flag `SYNC_DEACTIVATE=true`. Target set: users active in the platform and absent from the full enabled-user snapshot of Keycloak (the sync account itself and users with personal projects only keep the API's own refusals; personal projects are never removed).
- Guard against an incomplete snapshot: deactivation is not performed when any Keycloak read errored or was truncated (page ceiling hit), when Keycloak returned 0 users, or when the snapshot has fewer users than `SYNC_DEACTIVATE_MIN_USERS`. The run then follows the normal status rules (failed or skipped) and deactivates nobody.
- Per-run ceiling: the share of active platform users to deactivate may not exceed `SYNC_DEACTIVATE_MAX_RATIO` (default 0.10). Above it the run aborts before the first deactivation, nothing is deactivated, status `failed`. Dry run reports the planned count against the ceiling.

## Result, exit codes, counters

| Status | Condition | Exit |
|---|---|---|
| ok | all applied | 0 |
| skipped | job not enabled/configured, Keycloak unreachable, read error, or 0 users; nothing written; loud warning with a distinct log key per reason | 0 |
| failed | management API unreachable or 401/403 after one re-auth (run stops); at least one user failed with a non-permanent error (others still reconciled); deactivation ceiling exceeded | 1 |

- Known permanent refusals (409 for project creator, last administrator, similar) are counted as `refused`, do not make the run failed and do not change the exit code; they are listed in the log per user.
- Final stable line `sync_run_complete: status=<...>` with: created, updated, reactivated, deactivated, memberships_added, memberships_removed, admin_changes, refused, failures.

## Safeguards of the old job

| Old safeguard | Now |
|---|---|
| Personal projects never removed | kept (job filter and API refusal) |
| Empty Keycloak membership never wipes memberships | kept (job rule) |
| Missing email skips the user | kept |
| Admin flag unchanged when role read fails | kept |
| Maintainers never demoted | lost: `is_maintainer` is not in the API response; needs a platform change (out of scope) |
| Profile picture sync | lost: the upsert request has no picture field; needs a platform change (out of scope) |
| Project creator stays a member | enforced by the API; DELETE gets 409, counted as `refused` |
| Email owned by another id is skipped | partly: API error is a user failure unless it is a known permanent refusal; pre-detection from `GET /users` email data is done by the job |

## Authentication to the management API

- One Keycloak client (client_credentials) with its own Secret reads Keycloak and calls the platform API with one token; env `KEYCLOAK_ADMIN_CLIENT_ID`/`_SECRET` and `IDP_SYNC_API_URL`; token endpoint is the realm token endpoint. Its service account has the `realm-management` roles `view-users, query-users, query-groups, view-realm`.
- Required token claims (documented): `sub` (the Keycloak id of the service account, used to find its platform user), `preferred_username` equal to the platform's `IDP_SYNC_SERVICE_ACCOUNT_USERNAME` (`service-account-<client-id>`), `name` (needs a hardcoded-claim mapper for a service account), `realm_access.roles`, `user_type=service_account`; `email` optional.
- Basis: the installed `codemie_enterprise/idp/keycloak.py` does not verify the JWT signature, does not read `Authorization: Bearer`, and reads the token from `x-auth-request-access-token` (or base64 claims from `x-userinfo`). So the proxy in front of the backend is the only control.
- Operator precondition (not a platform change, not verifiable from this repo: `deploy-templates` has no auth-url, auth-response-headers or NetworkPolicy): `IDP_SYNC_API_URL` is the external address behind the proxy/ingress, which accepts the Bearer token and sets `x-auth-request-access-token`, and the backend is reachable only through that proxy. No concrete URL is invented here. Checklist in the docs: (1) request through `IDP_SYNC_API_URL` with the token succeeds; (2) the same request carrying a forged `x-auth-request-access-token` but no valid token is rejected; (3) a direct call to the backend with the token returns 401; (4) the owner of the chart or stand confirms the backend has no other path.
- Token handling: cached with a safety margin and re-acquired before expiry; explicit connect/read timeouts; a timeout is classified as unreachable (API: failed; Keycloak: skipped); a 302/401/403 from the proxy on a previously valid token triggers one token refresh and one retry, and only a repeat is a refusal of the account.

## Portability, packaging, retries

- No `codemie.*` or `codemie_tools.*` import in the job. Settings come from job-local env; a local httpx-only Keycloak client (~100 lines) replaces `KeycloakClient`, which is not edited. No sqlalchemy, asyncpg or langchain.
- Admin rule has one job-local env, `SYNC_ADMIN_ROLE_NAME` (equivalent of the platform's `ADMIN_ROLE_NAME`); the docs say it must equal the platform's. There is no job-side bootstrap admin: the platform keeps the rights of its `ADMIN_USER_ID` user on upsert, and the job deactivates that user only when Keycloak no longer lists it.
- `Dockerfile.sync` installs only the job's dependencies, copies only the job directory, drops `config/` and platform packages, and runs an import check with only the job's files where `codemie` and `codemie_tools` are blocked. Fallback if that check cannot pass because of the `codemie.enterprise` package chain: the job moves to a separate top-level package (the requester approved this in advance); the spec does not otherwise change.
- Job env: `SYNC_ENABLED`, `SYNC_DRY_RUN`, `SYNC_BATCH_SIZE`, `SYNC_MAX_PAGES` (default 2000), `SYNC_DEACTIVATE`, `SYNC_DEACTIVATE_MAX_RATIO`, `SYNC_DEACTIVATE_MIN_USERS`, plus `KEYCLOAK_ADMIN_*` and `IDP_SYNC_*`.
- CronJob: `concurrencyPolicy: Forbid`, `backoffLimit: 0`, `restartPolicy: Never`, `activeDeadlineSeconds` default 1800. Consequence: a failed run is not retried inside the tick; the next scheduled run is the retry, one alert per tick. Manual parallel runs are forbidden by the operator. A 60,000-user first sync may need a one-time larger deadline (about 240,000 writes, unmeasured); the docs describe how to raise it for that run.

## Cleanup and docs

- Remove from `src/codemie/configs/config.py` `KEYCLOAK_SYNC_ENABLED/SCHEDULE/BATCH_SIZE/DRY_RUN` (grep: consumers are only `entrypoint.py`, its tests and `sync-cronjob.yaml`). `KEYCLOAK_ADMIN_*` stay (used by `enterprise/migration`).
- `values.yaml` `keycloakSync`: remove `database.*` and unused keys; add `idpSync.*`, deactivation settings, admin rule values. CronJob drops `POSTGRES_*` and the customer ConfigMap mount. Breaking key changes are documented for the operator.
- `docs/operations/idp-managed-sync.md` is rewritten (build, schedule, both clients, proxy checklist, dry run, first live sync, rollback by suspending the CronJob).
- Tests reuse the Keycloak-read tests and `FakeKeycloak`; DB, lock and savepoint tests are replaced by `httpx.MockTransport` tests.

## Decisions

- Rework in place under `src/codemie/enterprise/sync/`, without platform imports, over a new sub-project or repository — user
- Local httpx Keycloak client over editing the shared `KeycloakClient` — user
- One client and Secret for reading and writing over two separate clients — the user chose less manual setup per environment over secret isolation.
- Management API reached through a proxy, as an operator precondition with a checklist, over naming a concrete URL or changing the platform — user, answers AR-1
- Deactivation flag kept, default off, with a defined target set, a per-run ceiling (default 10%) and an incomplete-snapshot guard, over dropping the flag — user, answers AR-2
- Known permanent API refusals counted as `refused` without failing the run over counting them as failures — user, answers AR-3
- `backoffLimit: 0` and `restartPolicy: Never` over the current retrying pod — user, answers AR-4
- Overlap protection by `concurrencyPolicy: Forbid` alone over a Lease or DB lock — user
- Token refresh with margin, timeouts, and one re-auth on 302/401/403 over treating them as account refusal — user, answers AR-5
- Admin rule through two job env fed from one values source over a platform-config dependency — user, answers AR-6
- Import check with blocked platform modules, with a top-level package as fallback, over an unchecked claim — user, answers AR-7
- Page ceiling `SYNC_MAX_PAGES` (default 2000) and `activeDeadlineSeconds` 1800 over fixed 1000 pages — user
- Keycloak unreachable/empty/not configured is `skipped` (exit 0) over `error`; API failure or user failure is `failed` — user
- Remove all settings the new job does not use over keeping them — user
- Diff against `GET /users` in the job over relying on an API "unchanged" signal

## Non-goals

- Any change to the platform API, guard, `IDP_SYNC_SERVICE_ACCOUNT_USERNAME`, `IDP_MANAGED_ENABLED`, switch locks, proxy or NetworkPolicy configuration.
- Platform changes for maintainers, pictures or the project-creator case.
- Editing `KeycloakClient` in `codemie_tools`.
- Real-time or webhook sync; sync run history in the platform.
- A new sub-project, repository or CI pipeline (except the top-level-package fallback).
- Changing `KEYCLOAK_ADMIN_*` or `enterprise/migration`.

## Acceptance criteria

1. No `codemie`/`codemie_tools` import in the job and no database connection; the import check runs with only the job's files and those modules blocked.
2. Equal Keycloak and platform state produce zero write calls.
3. Differences are applied only through `/v1/idp-sync/`; personal projects are untouched.
4. Deactivation is called only with the flag on; the target set is active platform users absent from the full Keycloak snapshot.
5. Keycloak unreachable/error/empty/not configured gives `skipped`, no writes, exit 0.
6. API unreachable or 401/403 after one re-auth stops the run: `failed`, exit 1; a non-permanent user failure leaves others reconciled, `failed`, exit 1; permanent refusals only raise `refused` and keep exit 0.
7. Incomplete snapshot (read error, truncation, 0 users, below minimum) never deactivates anyone; exceeding `SYNC_DEACTIVATE_MAX_RATIO` aborts before the first deactivation with status `failed`.
8. Dry run logs every would-be change and makes no write call.
9. `sync_run_complete` carries the status and all counters including `refused`.
10. Token is refreshed before expiry and once on 302/401/403; timeouts are explicit and classified.
11. CronJob has `Forbid`, `backoffLimit: 0`, `restartPolicy: Never`, deadline 1800; ceiling configurable (default 2000); admin rule env come from one values source.
12. `KEYCLOAK_SYNC_*` removed from `config.py`; `database.*` removed from values; slim `Dockerfile.sync` builds.
13. Docs contain the claims list, a table of all job env variables, a status/exit-code table identical to criteria 5 and 6, the proxy checklist (forged header rejected, direct backend call 401), dry run, first sync and rollback.
