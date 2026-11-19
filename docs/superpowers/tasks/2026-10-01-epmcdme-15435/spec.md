# IdP-Managed Users — Backend Spec (EPMCDME-15435)

## Overview

This spec covers three sequential sub-features that together make Keycloak the authoritative source
of truth for all user identities and project access in the platform:

1. **Mutation Guard** — a feature switch (`IDP_MANAGED_ENABLED`) that rejects every in-platform
   user and membership write when enabled.
2. **Keycloak Sync** — an idempotent, scheduled reconciliation job that creates and updates users,
   memberships, and budget memberships from Keycloak.
3. **Sync Docker Image** — a minimal, independently deployable image that runs the sync process and
   nothing else.

The complexity assessment (XL, score 28) recommends implementing these as three sequential slices:
mutation guard first (security foundation), sync coordinator second (core reconciliation), sync
image third (packaging and deployment). Each slice is independently releasable.

---

## Slice 1 — Mutation Guard

### Feature Switch

A new boolean config var `IDP_MANAGED_ENABLED` (default `False`) is added to
`src/codemie/configs/config.py`, grouped alongside `KEYCLOAK_MIGRATION_ENABLED`. When `False`, no
behavior changes anywhere in the platform.

### Guarded Paths

When `IDP_MANAGED_ENABLED` is `True`, every write path for users and memberships returns HTTP 403
with the body `{"detail": "User management is controlled by the identity provider."}`.

The guard is applied at the router entry of every mutation path in
`src/codemie/rest_api/routers/user_management_router.py`: all POST, PUT, and DELETE operations for
users, project memberships, KB access assignments, and budget assignments (10+ paths). The guard
also applies to first-login user creation in `AuthenticationService.create_user_from_idp` and to
the grant/revoke paths in `UserAccessService`. These two service-layer guards prevent
non-router creation vectors from bypassing the flag.

Read-only endpoints (GET), budget management endpoints, and any endpoint that does not mutate user
identity or membership are unaffected regardless of switch state.

### Public Configuration

The current boolean value of `IDP_MANAGED_ENABLED` is added to the platform's public configuration
response (the existing deployment-metadata endpoint). No authentication change is needed; this is a
read.

### Testing

Unit tests in `tests/codemie/rest_api/` cover every guarded path: asserting HTTP 403 when the flag
is on, and asserting the normal response when it is off. Service-layer guard tests follow the
`@patch(f"{MODULE}.config")` pattern established in
`tests/unit/service/leaderboard/test_scheduler.py`.

---

## Slice 2 — Keycloak Sync Coordinator

### New Config Vars

Four new env vars are added to `src/codemie/configs/config.py`, grouped near the existing
Keycloak block:

| Variable | Default | Role |
|---|---|---|
| `KEYCLOAK_SYNC_ENABLED` | `False` | Sync on/off |
| `KEYCLOAK_SYNC_SCHEDULE` | `"*/10 * * * *"` | Cron expression, consumed by K8s CronJob via Helm value |
| `KEYCLOAK_SYNC_BATCH_SIZE` | `100` | Users per Keycloak page |
| `KEYCLOAK_SYNC_DRY_RUN` | `False` | Log planned changes; write nothing |

### KeycloakClient Extension

`src/codemie_tools/access_management/keycloak/keycloak_client.py` gains two capabilities required
to process 60,000 users within a 10-minute window:

- **Token reuse**: a single client-credentials token is acquired at sync-run start and reused for
  all subsequent calls; re-acquired only on a 401 response.
- **Paginated listing**: a new `list_users(first, max)` method iterates
  `/admin/realms/{realm}/users?first=N&max=BATCH_SIZE` until an empty page is returned, yielding
  all enabled users. Token reuse applies across all pages.

### Sync Coordinator

A new `KeycloakSyncCoordinator` is implemented in `src/codemie/enterprise/sync/coordinator.py`,
following the same deps-injection protocol pattern as
`src/codemie/enterprise/migration/coordinator.py`. It accepts a `CodemieSyncDeps` struct (user,
user_project, user_kb, application repositories; `PersonalProjectService`;
`DynamicConfigService`) and a `KeycloakClient` instance. It does **not** reuse
`KeycloakMigrationCoordinator`, which contains non-idempotent personal-project creation and
lock-state-machine steps incompatible with repeated runs.

**Run sequence:**

1. Acquire a PostgreSQL advisory lock via `async_leader_lock` using a lock ID distinct from the
   migration coordinator's `7734269302`. If the lock is already held, skip the run and log as
   skipped; do not error.
2. Fetch all enabled Keycloak users via paginated listing. If Keycloak is unreachable, returns an
   empty set, or is not configured: skip the run with no DB writes and status `skipped`.
3. For each Keycloak user (per-user failure is isolated; the run continues):
   - **User upsert**: create `UserDB` if absent (name, email, picture, platform-admin status).
     Update `name`, `email`, `picture`, `platform_admin` if any field differs. Reactivate if
     `is_active=False` in the platform but the Keycloak user is enabled.
   - **Project membership upsert**: resolve the user's Keycloak group/attribute assignments to
     platform project IDs. Add missing `UserProject` rows; remove rows not present in Keycloak,
     unless the project is personal (personal memberships are never removed). Apply project-admin
     flag changes.
   - **Budget membership sync**: for each project membership add/remove, call a new pure-async
     budget sync path in the coordinator rather than
     `ProjectAssignmentService._sync_project_budget_member_added`, which uses an
     `asyncio.run_coroutine_threadsafe` bridge incompatible with a standalone async process.
   - **Project creation**: if a Keycloak group maps to a project that does not exist in the
     platform, create it using the platform's default project-creation settings. No Keycloak group
     metadata is mapped to project attributes.
   - Platform-local accounts (users absent from or disabled in Keycloak) are left entirely
     untouched.
4. In dry-run mode (`KEYCLOAK_SYNC_DRY_RUN=True`): perform all reads and compute the full diff;
   issue no DB writes; log what would have changed.
5. Log run summary: counts of created/updated/reactivated users; memberships added/removed; budget
   memberships synced; per-user failures; skip status and reason.

**Idempotency**: every write decision is preceded by a read of current state. If the platform DB
already matches Keycloak, no writes are issued.

### Sync Entrypoint

`src/codemie/enterprise/sync/entrypoint.py` is the `__main__`-compatible module invoked by the
sync container. It validates required env vars, constructs a DB session and `KeycloakClient`,
instantiates and runs `KeycloakSyncCoordinator.run()`, and exits with code `0` on success or skip
and code `1` on an unrecoverable error (e.g., DB unreachable). No web server, APScheduler, or AI
stack is imported.

### Testing

Tests in `tests/codemie/enterprise/sync/` cover `KeycloakSyncCoordinator` using `AsyncMock` for
all repository and client boundaries. Cases include: user create, user update (all four fields),
user reactivation, membership add, membership remove (non-personal), personal membership
preservation, budget sync call, project creation, per-user failure isolation, full skip on
Keycloak unreachable, dry-run (no writes), idempotent run (no writes when already in sync), and
advisory lock skip. Tests for `KeycloakClient` pagination cover page boundary and empty-page
termination.

---

## Slice 3 — Sync Docker Image

### Dockerfile.sync

A new `Dockerfile.sync` at the repo root uses a multi-stage build:

- **Builder stage**: starts from the same base Python image as the main `Dockerfile`, installs the
  full `pyproject.toml` dependency set via Poetry, and produces a complete virtual environment.
- **Final stage**: starts from a slim Python base image, copies only the sync entrypoint module
  (`src/codemie/enterprise/sync/`), its direct platform module dependencies (repositories, models,
  config, `codemie_tools/access_management/keycloak/`), and their required third-party packages
  (SQLModel, asyncpg, httpx, pydantic-settings) from the builder's virtual environment. No web
  server, LangChain, LiteLLM, Elasticsearch client, NATS client, or Azure Storage SDK is present
  in the final layer.
- Sets `CMD ["python", "-m", "codemie.enterprise.sync.entrypoint"]`.
- Includes a drift-check `RUN` step in the final stage that imports `KeycloakSyncCoordinator` and
  all direct coordinator dependencies, failing the build with a named import error if any required
  module has been moved or removed.

### Deployment Manifest

A new Helm template in `deploy-templates/templates/` defines a Kubernetes `CronJob`:
- `schedule` is set from the `KEYCLOAK_SYNC_SCHEDULE` Helm value.
- `concurrencyPolicy: Forbid` prevents overlapping invocations at the K8s level (the advisory lock
  provides a second layer of overlap protection).
- `restartPolicy: OnFailure` with a low `backoffLimit` so a transient Keycloak outage yields a
  logged skip, not an unrecoverable pod failure.
- Keycloak admin credentials (`KEYCLOAK_ADMIN_CLIENT_ID`, `KEYCLOAK_ADMIN_CLIENT_SECRET`) are
  projected only into the CronJob pod's env. They are absent from the main API `Deployment`
  manifest.

### Operator Documentation

A short operator guide in `docs/operations/idp-managed-sync.md` covers:
- Enabling the feature: set `IDP_MANAGED_ENABLED=true`, `KEYCLOAK_SYNC_ENABLED=true`, supply
  Keycloak admin credentials to the sync workload.
- Running a dry run first: set `KEYCLOAK_SYNC_DRY_RUN=true` and trigger the CronJob manually;
  review the log summary before enabling live writes.
- Rollback: set `IDP_MANAGED_ENABLED=false` to restore in-platform mutations immediately; disable
  or delete the CronJob to stop sync runs. No database migration is required for either direction.

---

## Non-Goals

- Deactivating or soft-deleting platform users who are removed or disabled in Keycloak.
- Syncing the legacy knowledge-base access list.
- Switching from legacy to persistent user management mode as part of this feature.
- Changing how platform-admin status is derived from Keycloak attributes (existing logic is
  unchanged).
- Editing Keycloak data from the platform.
- Hiding or disabling admin UI controls in the frontend.
- Real-time or webhook-driven sync; this spec covers scheduled polling only.
- A UI for sync run history or status.

---

## Decisions

1. Sync runs as a standalone one-shot container (K8s CronJob) rather than an APScheduler in the
   API pod — the task explicitly requires a separately deployed minimal image with no web server.
2. New `KeycloakSyncCoordinator` over reusing `KeycloakMigrationCoordinator` — the migration
   coordinator has non-idempotent personal-project and lock-state-machine steps that corrupt state
   under repeated runs.
3. Pure-async budget sync path over the existing `asyncio.run_coroutine_threadsafe` bridge — the
   bridge requires a running app event loop that does not exist in the standalone sync process.
4. Token reuse and paginated listing added to `KeycloakClient` — at one fresh token per call and
   100 users/page, 60,000 users would require 600 auth round-trips, breaching the 10-minute window.
5. Distinct advisory lock ID over reusing `7734269302` — lock collision with the migration
   coordinator would cause spurious sync skips during platform startup.
6. Multi-stage Docker build over a new Poetry sync dependency group — the builder stage resolves
   the full dependency tree; the final stage copies only the sync modules and their required
   packages, producing a slim image without requiring a new Poetry group in pyproject.toml.
7. Default project creation settings for Keycloak-sourced projects over mapping Keycloak group
   metadata — the task does not define a Keycloak group attribute schema for project properties;
   mapping is out of scope.
