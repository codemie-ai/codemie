# IdP-Managed Users — Backend Implementation Plan (EPMCDME-15435)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Implement a mutation guard (`IDP_MANAGED_ENABLED`) that rejects in-platform user/membership writes, a scheduled idempotent Keycloak-to-platform sync coordinator, and a minimal Docker image that runs the sync as a standalone K8s CronJob.

**Architecture:** Three sequential slices — mutation guard establishes the security foundation; sync coordinator adds idempotent reconciliation via `KeycloakSyncCoordinator` and an extended `KeycloakClient`; Docker image packages the coordinator without the web or AI stacks. The sync runs as a one-shot container (CronJob), not APScheduler in the API pod; a PostgreSQL advisory lock prevents overlap across pods.

**Tech Stack:** FastAPI, SQLModel/asyncpg, httpx, pydantic-settings, pytest-asyncio.

**Spec:** `docs/superpowers/tasks/2026-10-01-epmcdme-15435/spec.md`

Commit per task using the repository's existing convention.

## Global Constraints

- `IDP_MANAGED_ENABLED` default `False` — no behavior change unless explicitly enabled.
- Guard response: HTTP 403 `{"detail": "User management is controlled by the identity provider."}`.
- GET endpoints, budget management endpoints, and non-membership endpoints are never guarded.
- Platform-local accounts (absent from Keycloak) are left entirely untouched by the sync.
- KB access list sync is out of scope.
- Sync coordinator advisory lock ID must differ from migration coordinator's `7734269302`.
- Final sync Docker layer: no uvicorn, LangChain, LiteLLM, Elasticsearch client, NATS, or Azure Storage SDK.
- `KEYCLOAK_SYNC_SCHEDULE` default: `"*/10 * * * *"`.

## Review Focus

- GET endpoints must not be blocked when `IDP_MANAGED_ENABLED=True`: Task 1 tests assert that GET handlers return their normal response code even when the flag is on.
- Keycloak-absent users: platform-local accounts must remain untouched; Task 5 tests confirm `_reconcile_user` is only called for users returned by Keycloak, never for DB-only users.
- Advisory lock skip: if the lock is already held, `run()` must return `SyncResult(status="skipped")` and log, not raise; Task 4 tests cover this path explicitly.
- Per-user failure isolation: a single Keycloak user whose reconciliation raises must not abort the run; Task 5 tests assert the failure counter increments and the loop continues.
- Dry-run completeness: all reads and diff computation must occur but zero DB writes may be issued; Task 7 tests assert every repository write method was called zero times in dry-run mode.

---

### Task 1: `IDP_MANAGED_ENABLED` config flag + router mutation guard

**Files:**
- Modify: `src/codemie/configs/config.py` — add `IDP_MANAGED_ENABLED: bool = False` after `KEYCLOAK_MIGRATION_ENABLED`
- Modify: `src/codemie/rest_api/routers/user_management_router.py` — add `require_no_idp_lock` dependency; apply to every POST, PUT, DELETE handler
- Test: `tests/codemie/rest_api/test_user_management_router_idp_guard.py`

**Test-first: yes — `test_create_user_returns_403_when_idp_managed_enabled` (and one per mutation path) fail before the dependency exists**

New dependency (new symbol — include in router module):

```python
def require_no_idp_lock():
    if config.IDP_MANAGED_ENABLED:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User management is controlled by the identity provider.",
        )
```

Add `Depends(require_no_idp_lock)` to every POST/PUT/DELETE handler. Leave all GET handlers and budget-only endpoints untouched.

Tests use `@patch("codemie.rest_api.routers.user_management_router.config")` with `IDP_MANAGED_ENABLED=True` for the 403 assertion, and `IDP_MANAGED_ENABLED=False` for the normal-path assertion (2xx or validation error — anything but 403).

---

### Task 2: Service-layer guards + public config exposure

**Files:**
- Modify: `src/codemie/service/user/authentication_service.py:create_user_from_idp` — add `IDP_MANAGED_ENABLED` guard at entry
- Modify: `src/codemie/service/user/user_access_service.py` — add guard at entry of grant-project, revoke-project, grant-KB, revoke-KB methods
- Modify: deployment-metadata/public-config endpoint (locate via `grep -r "deployment\|public.*config" src/codemie/rest_api/routers/`) — add `idp_managed_enabled: bool` field to response model and populate from `config.IDP_MANAGED_ENABLED`
- Test: `tests/codemie/service/user/test_idp_managed_service_guards.py`; add assertion to existing public-config test

**Test-first: yes — `test_create_user_from_idp_raises_when_idp_managed` and `test_grant_project_raises_when_idp_managed` fail before the guards exist**

In each guarded method add: `if config.IDP_MANAGED_ENABLED: raise HTTPException(403, detail="User management is controlled by the identity provider.")` following the `IDP_PROVIDER != "local"` pattern already in `update_user_fields`. Use `@patch(f"{MODULE}.config")` in tests.

---

### Task 3: Sync config vars + `KeycloakClient` token reuse and pagination

**Files:**
- Modify: `src/codemie/configs/config.py` — add `KEYCLOAK_SYNC_ENABLED`, `KEYCLOAK_SYNC_SCHEDULE`, `KEYCLOAK_SYNC_BATCH_SIZE`, `KEYCLOAK_SYNC_DRY_RUN` near existing Keycloak block
- Modify: `src/codemie_tools/access_management/keycloak/keycloak_client.py` — add `_token: str | None = None`; extend `execute_request` to reuse token and re-acquire on 401; add `list_users`
- Test: `tests/codemie_tools/access_management/keycloak/test_keycloak_client_pagination.py`

**Test-first: yes — `test_list_users_paginates_until_empty_page` and `test_list_users_token_reused_across_pages` fail before the method exists**

New method on `KeycloakClient`:

```python
def list_users(self, batch_size: int = 100) -> list[dict]:
    users, first = [], 0
    while True:
        page = self.execute_request(
            "GET", f"/admin/realms/{self._realm}/users",
            params={"first": first, "max": batch_size, "enabled": True},
        )
        if not page:
            break
        users.extend(page)
        first += len(page)
    return users
```

Tests mock `execute_request` to return two pages then `[]`; assert total count equals page1+page2 and `execute_request` was called three times. Token-reuse test mocks the auth fetch and asserts it is called exactly once across a two-page run.

---

### Task 4: `KeycloakSyncCoordinator` scaffold — advisory lock, skip logic

**Files:**
- Create: `src/codemie/enterprise/sync/__init__.py`
- Create: `src/codemie/enterprise/sync/coordinator.py`
- Test: `tests/codemie/enterprise/sync/test_coordinator_lifecycle.py`

**Test-first: yes — `test_run_skips_when_lock_held` and `test_run_skips_when_keycloak_unreachable` fail before the coordinator exists**

New symbols:

```python
@dataclass
class CodemieSyncDeps:
    user_repository: Any
    user_project_repository: Any
    user_kb_repository: Any
    application_repository: Any
    personal_project_service: Any
    dynamic_config_service: Any

@dataclass
class SyncResult:
    status: str  # "ok" | "skipped" | "error"
    users_created: int = 0
    users_updated: int = 0
    users_reactivated: int = 0
    memberships_added: int = 0
    memberships_removed: int = 0
    budgets_synced: int = 0
    user_failures: int = 0
```

`KeycloakSyncCoordinator(deps, kc_client)`. `async run(batch_size, dry_run) -> SyncResult`: acquire `async_leader_lock` with ID `hash("keycloak_sync_coordinator") & 0x7FFFFFFFFFFFFFFF` (differs from migration lock `7734269302`); if not acquired log "sync skipped: lock held" and return `SyncResult(status="skipped")`. If `kc_client.list_users()` raises or returns empty, return `SyncResult(status="skipped")`. Tests mock `async_leader_lock` as an `AsyncMock` context manager; follow `AsyncMock` pattern from `tests/codemie/service/user/test_authentication_service.py`.

---

### Task 5: User reconciliation (create, update, reactivate)

**Files:**
- Modify: `src/codemie/enterprise/sync/coordinator.py` — add `_reconcile_user` method
- Test: `tests/codemie/enterprise/sync/test_coordinator_users.py`

**Test-first: yes — `test_reconcile_creates_new_user`, `test_reconcile_updates_changed_fields`, `test_reconcile_reactivates_inactive_user`, `test_reconcile_isolates_per_user_failure` all fail before the method exists**

`_reconcile_user(self, kc_user: dict, dry_run: bool) -> dict` returns per-user counts rolled into `SyncResult`. Logic: look up user by `kc_user["email"]` via `user_repository`; if absent create `UserDB` (name, email, picture, platform_admin); if present and `is_active=False` reactivate; if present and active update name/email/picture/platform_admin only if any field differs. In dry-run mode skip all writes and log the planned change. Wrap body in `try/except Exception`; on exception increment `user_failures` and continue (do not re-raise). Platform-local users are never iterated here; this method is only called per Keycloak user.

---

### Task 6: Membership, budget, and project creation reconciliation

**Files:**
- Modify: `src/codemie/enterprise/sync/coordinator.py` — add `_reconcile_memberships` and `_sync_budget` methods; wire them into `run()`
- Test: `tests/codemie/enterprise/sync/test_coordinator_memberships.py`

**Test-first: yes — `test_membership_add_missing`, `test_membership_remove_non_personal`, `test_personal_membership_preserved`, `test_budget_sync_called_on_add`, `test_project_created_for_unknown_group` all fail before the methods exist**

`_reconcile_memberships(self, user_db, kc_project_ids, dry_run)`: fetch current `UserProject` rows; add rows present in `kc_project_ids` but absent in platform; remove rows absent from `kc_project_ids` unless `project.is_personal` (personal memberships are never removed); on each add call `await self._sync_budget(user_db, project_id, dry_run)`. `_sync_budget` is pure async — calls the budget repository directly, no `asyncio.run_coroutine_threadsafe`. If a Keycloak group maps to a project absent in the platform, create it via `application_repository` using default settings. KB access list sync is excluded (non-goal — `user_kb_repository` is not written by this method).

---

### Task 7: Dry-run mode + sync entrypoint

**Files:**
- Modify: `src/codemie/enterprise/sync/coordinator.py` — log `SyncResult` summary at `INFO` level after run
- Create: `src/codemie/enterprise/sync/entrypoint.py`
- Test: `tests/codemie/enterprise/sync/test_entrypoint.py`; `tests/codemie/enterprise/sync/test_coordinator_dryrun.py`

**Test-first: yes — `test_dryrun_issues_no_db_writes` and `test_entrypoint_exits_1_on_error` fail before the implementations exist**

Dry-run tests assert every `user_repository` and `user_project_repository` write method (`AsyncMock`) was called zero times when `dry_run=True`.

New module entrypoint (new symbol):

```python
async def main() -> None:
    kc_client = KeycloakClient(...)
    deps = CodemieSyncDeps(...)
    coordinator = KeycloakSyncCoordinator(deps, kc_client)
    result = await coordinator.run(
        batch_size=config.KEYCLOAK_SYNC_BATCH_SIZE,
        dry_run=config.KEYCLOAK_SYNC_DRY_RUN,
    )
    if result.status == "error":
        raise SystemExit(1)

if __name__ == "__main__":
    asyncio.run(main())
```

Validate required env vars (`KEYCLOAK_ADMIN_URL`, `KEYCLOAK_ADMIN_REALM`, `KEYCLOAK_ADMIN_CLIENT_ID`, `KEYCLOAK_ADMIN_CLIENT_SECRET`, and DB connection vars) at startup; `raise SystemExit(1)` with a descriptive message if any are missing. No APScheduler, uvicorn, or AI stack imports. Entrypoint tests mock `KeycloakSyncCoordinator.run` and check `SystemExit` code for each `SyncResult.status`.

---

### Task 8: `Dockerfile.sync` — multi-stage build

**Files:**
- Create: `Dockerfile.sync` (repo root)

**Test-first: no**

Multi-stage build. Builder stage: same base Python image as the main `Dockerfile`; install via Poetry to produce `/app/.venv`. Final stage: slim Python base; copy from builder only the packages needed (sqlmodel, asyncpg, httpx, pydantic-settings); copy source dirs `src/codemie/enterprise/sync/`, `src/codemie/configs/`, `src/codemie/rest_api/models/`, `src/codemie_tools/access_management/keycloak/`, `src/codemie/utils/leader_lock.py`, and repository modules used by the coordinator. Do not copy `src/codemie/rest_api/main.py` or any LangChain/LiteLLM/Elasticsearch directories. Add drift-check `RUN`: `python -c "from codemie.enterprise.sync.coordinator import KeycloakSyncCoordinator; from codemie.enterprise.sync.entrypoint import main"` — fails the build if a required module was moved. Set `CMD ["python", "-m", "codemie.enterprise.sync.entrypoint"]`.

---

### Task 9: Helm CronJob manifest + operator documentation

**Files:**
- Create: `deploy-templates/templates/sync-cronjob.yaml`
- Create: `docs/operations/idp-managed-sync.md`

**Test-first: no**

`sync-cronjob.yaml`: `kind: CronJob`, `schedule: {{ .Values.keycloakSync.schedule | default "*/10 * * * *" }}`, `concurrencyPolicy: Forbid`, `restartPolicy: OnFailure`, `backoffLimit: 2`. Project `KEYCLOAK_ADMIN_CLIENT_ID` and `KEYCLOAK_ADMIN_CLIENT_SECRET` from a Secret into the CronJob pod env only — absent from the main API `Deployment` manifest.

`docs/operations/idp-managed-sync.md`: cover enabling the feature (`IDP_MANAGED_ENABLED=true`, `KEYCLOAK_SYNC_ENABLED=true`, supply Keycloak admin credentials to the sync workload); running a dry run first (`KEYCLOAK_SYNC_DRY_RUN=true`, trigger CronJob manually, review log summary); rollback (set `IDP_MANAGED_ENABLED=false` to restore in-platform mutations immediately; disable or delete the CronJob to stop sync; no DB migration required for either direction).

---

## Negative constraints

| Constraint (from spec Non-goals / explicit rules) | Task that honors it |
|---|---|
| No user deactivation for Keycloak-absent users | Task 5 — `_reconcile_user` iterates only Keycloak users; local-only users are never touched |
| No KB access list sync | Task 6 — `user_kb_repository` is not written by `_reconcile_memberships` |
| No change to platform-admin derivation | Task 5 — maps `platform_admin` field using existing derivation; no schema change |
| No Keycloak data edits from platform | No task writes to Keycloak; all writes are DB-side |
| No frontend changes | No frontend tasks present |
| No real-time or webhook sync | Task 9 uses CronJob (polling only); no webhook handler |
| No UI for sync history | No UI tasks present |
| GET endpoints unguarded | Task 1 — `require_no_idp_lock` added only to POST/PUT/DELETE handlers |
| Budget management endpoints unguarded | Task 1 — budget-only endpoints excluded from guard |
| No web server or AI stack in sync image | Task 8 — `main.py`, LangChain, Elasticsearch dirs not copied to final stage |
