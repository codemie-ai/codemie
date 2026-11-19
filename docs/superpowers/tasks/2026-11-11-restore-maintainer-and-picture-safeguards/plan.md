# Restore maintainer and picture safeguards Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Server never demotes a maintainer via `/v1/idp-sync/` upsert, and `picture` syncs idempotently from Keycloak through the job.

**Architecture:** Pydantic models gain optional `picture` and read-only `is_maintainer`; `IdpSyncService` computes effective admin once, after loading the user; the separate `idp_sync_job` package computes its own effective admin in `UserState.from_keycloak` and carries picture through diff and payload.

**Tech Stack:** Python, FastAPI, SQLModel, pytest (in-memory SQLite), httpx fakes in `tests/idp_sync_job/`.

**Spec:** `docs/superpowers/tasks/2026-11-11-restore-maintainer-and-picture-safeguards/spec.md`

Commit per task using the repository's existing convention (follow `git log`, no AI footer).

## Global Constraints

- `PICTURE_MAX_LENGTH = 1024` in `src/codemie/rest_api/models/idp_sync.py`; the job keeps its own copy (must not import `codemie`; `tests/idp_sync_job/test_import_purity.py` must pass).
- All contract changes additive. No migration, no `UserDB` change.
- Do not touch: guard, `IDP_SYNC_SERVICE_ACCOUNT_USERNAME`, `IDP_MANAGED_ENABLED`, mode locks, `authenticate`, `UserManagementService`, login sync, the 2026-11-10 spec file.
- Empty/omitted/None picture never clears; stored `None` and `""` are equal.

## Review Focus

- Picture of exactly 1024 chars accepted, 1025 -> 422 (Task 1).
- Legacy maintainer row with `is_admin=False` is healed to True, no refusal log (Task 2).
- Reactivated maintainer with `is_admin=false` keeps admin and `project_limit` (Task 2).
- Maintainer with Keycloak `is_admin=false` causes zero PUTs on the second run (Task 5).
- Keycloak user with no `picture` attribute or `[]` yields no diff, no payload key (Task 4).

---

### Task 1: API models and picture-length contract

**Files:**
- Modify: `src/codemie/rest_api/models/idp_sync.py` (`IdpUserUpsertRequest`, `IdpUserResponse`, ~lines 40-72)
- Test: `tests/codemie/rest_api/models/test_idp_sync_models.py`

**Interfaces:**
- Produces: `PICTURE_MAX_LENGTH: int = 1024`; `IdpUserUpsertRequest.picture: str | None = Field(None, max_length=PICTURE_MAX_LENGTH, description=...)`; `IdpUserResponse.picture: str | None = None`; `IdpUserResponse.is_maintainer: bool = False` (read-only, described).

- [ ] Write failing tests: 1024-char picture valid, 1025 raises `ValidationError`; request without picture valid; response fields have non-empty `description`; `PICTURE_MAX_LENGTH` equals the length in `sa.Column("picture", sa.String(N))` parsed by regex from `src/external/alembic/versions/299a10eb05a0_add_user_management_tables.py` source (line ~39).
- [ ] Run `pytest tests/codemie/rest_api/models/test_idp_sync_models.py -v`; expect FAIL.
- [ ] Add the constant and fields; descriptions state "omitted/empty leaves the stored picture alone" and "maintainers always report is_admin true".
- [ ] Run the same tests; expect PASS.

Test-first: yes — 1025-char picture must raise ValidationError and constant must equal migration length.

### Task 2: Service: maintainer rule, picture, response

**Files:**
- Modify: `src/codemie/service/user/idp_sync_service.py` (`_to_response` 88-102, `_create` 146-166, `_apply_update` 186-216, `upsert_user` 219-233)
- Test: `tests/codemie/service/user/test_idp_sync_service.py`, `tests/codemie/rest_api/routers/test_idp_sync_router.py`

**Interfaces:**
- Consumes: Task 1 model fields.
- Produces: `IdpSyncService._effective_admin(user: UserDB, req: IdpUserUpsertRequest) -> bool` = `req.is_admin OR ADMIN_USER_ID match OR bool(user.is_maintainer)`; logs `idp_sync_maintainer_demotion_refused` only when `req.is_admin` is false and `user.is_admin` is true for a maintainer.

- [ ] Write failing tests using `_req`/`_seed_user` helpers: (a) maintainer + `is_admin=false` keeps `is_admin=True` and `project_limit`, `lock_and_count_active_admins` not called, no 409 even as last admin; (b) same for a reactivated maintainer; (c) legacy maintainer stored `is_admin=False` becomes True, no refusal log; (d) refusal log present for request false/stored true, absent otherwise; (e) non-maintainer demotion and last-admin 409 unchanged, `ADMIN_USER_ID` and create unchanged; (f) picture: new value written, event attributes contain `picture`; same value -> no write, no event; omitted/None/"" keep stored (stored None vs "" equal); create stores non-empty picture; (g) response carries `picture` and `is_maintainer` (service and router test).
- [ ] Run `pytest tests/codemie/service/user/test_idp_sync_service.py tests/codemie/rest_api/routers/test_idp_sync_router.py -v`; expect FAIL.
- [ ] In `upsert_user`: keep create path using the existing pre-load value; for the update path compute `_effective_admin` after loading and pass it as `target_admin` to `_apply_update` (existing admin/`project_limit` branch at 194-198 then handles heal and no-op). In `_apply_update` add picture to `changes` only when `req.picture` is truthy and differs from `user.picture or ""`; add `picture` to the event attribute whitelist (line 213). `_create` stores `req.picture or None`. `_to_response` maps `picture` and `is_maintainer`.
- [ ] Run the tests; expect PASS.

Test-first: yes — maintainer upsert with is_admin=false must keep is_admin and project_limit.

### Task 3: OpenAPI baseline

**Files:**
- Modify: `tests/codemie/rest_api/router_guards/baselines/openapi.json` (idp-sync path entries ~60704, 60773, 60891 and the `IdpUserUpsertRequest`/`IdpUserResponse` schemas only)
- Test: `tests/codemie/rest_api/router_guards/test_openapi_snapshot.py`

- [ ] Run the snapshot test; expect FAIL on idp-sync diffs.
- [ ] Generate the current schema (via `regenerate_baselines.py` into a temp copy, not over the baseline) and hand-merge only the idp-sync paths and the two schemas; do not accept removed routes.
- [ ] Run the snapshot test; expect PASS.

Test-first: no — baseline is a snapshot fixture driven by the existing snapshot test.

### Task 4: Job picture sync

**Files:**
- Modify: `src/idp_sync_job/keycloak_source.py` (`KeycloakUser` 55-66, `_to_user` 261-278), `src/idp_sync_job/state.py` (`UserState`, `profile_differs`, `upsert_payload`), `src/idp_sync_job/platform_client.py` (`PlatformUser`, `_to_user`)
- Test: `tests/idp_sync_job/test_keycloak_source.py`, `test_reconcile.py`, `test_platform_client.py`, fakes `keycloak_fake.py`, `platform_fake.py`

**Interfaces:**
- Produces: `KeycloakUser.picture: str = ""`; `PlatformUser.picture: str = ""` (from `.get("picture") or ""`); `UserState.picture: str`; `PICTURE_MAX_LENGTH = 1024` local to `state.py`; `upsert_payload()` includes `picture` only when non-empty and `len <= 1024`, otherwise logs a warning for an over-long one and omits it.

- [ ] Write failing tests: first element of `attributes["picture"]` is read; absent/`[]` gives `""`; `profile_differs` true when Keycloak picture non-empty and differs from platform; false when Keycloak picture empty (never a difference); payload omits empty picture; 1024 included, 1025 dropped with warning and other fields still sent; no change -> zero PUT calls (coordinator-level with fakes; extend fakes to carry picture/attributes).
- [ ] Run `pytest tests/idp_sync_job -v`; expect FAIL.
- [ ] Implement per Interfaces; diff compares picture only when the desired one is non-empty and within the limit.
- [ ] Run `pytest tests/idp_sync_job -v` including `test_import_purity.py`; expect PASS.

Test-first: yes — Keycloak user with a picture attribute must produce a payload containing it and a diff against an empty platform picture.

### Task 5: Job maintainer awareness

**Files:**
- Modify: `src/idp_sync_job/platform_client.py` (`PlatformUser.is_maintainer: bool | None`, None when field missing), `src/idp_sync_job/state.py` (`UserState.from_keycloak(kc_user, platform_user)`), `src/idp_sync_job/reconcile.py:129-146` and its callers, run report (counter `demotions_skipped_unknown_maintainer`)
- Test: `tests/idp_sync_job/test_reconcile.py`, `test_platform_client.py`, `test_coordinator.py`; helpers `kc()`/`pf()`, `platform_fake.py` (response items gain `is_maintainer`)

**Interfaces:**
- Produces: `UserState.from_keycloak(user: KeycloakUser, platform: PlatformUser | None = None) -> UserState` — the only place computing `kc.is_admin OR platform.is_maintainer`; when `platform.is_maintainer is None` and platform `is_admin` is true while Keycloak is false, keeps `True` and logs one warning `maintainer_unknown` per user per run, incrementing the report counter.

- [ ] Write failing tests: maintainer with Keycloak `is_admin=false` -> no UPDATE planned, no `admin_change`; second full run makes zero PUTs; non-maintainer demotion still planned; response lacking `is_maintainer` -> no true-to-false change sent, one `maintainer_unknown` warning, counter incremented, other changes (e.g. name) still sent.
- [ ] Run `pytest tests/idp_sync_job -v`; expect FAIL.
- [ ] Implement; reconcile logic stays unchanged on the resulting `is_admin`; update all `from_keycloak` call sites to pass the platform user (CREATE passes None).
- [ ] Run `pytest tests/idp_sync_job -v`; expect PASS.

Test-first: yes — a maintainer whose Keycloak is_admin is false must plan zero changes.

### Task 6: Operations guide

**Files:**
- Modify: `docs/operations/idp-managed-sync.md` (table at ~256-263)

- [ ] Remove the two rows at ~260-261 from "Safeguards of the old job that are lost".
- [ ] Describe: maintainer rule and `is_maintainer` in the response; rollout order "API first, then job"; new job against old API loses picture silently (known limitation) and sends no demotion (`maintainer_unknown`); maintainer-grant race (unlocked read, self-corrects next run); picture sources in managed mode: login sync (`authentication_service.py:318`, mirrors with `!=`, can erase) and the job both write, last writer wins, job restores on next run; clearing a picture is not done by sync (future clearing would need omitted vs explicit empty via `model_fields_set`).

Test-first: no — documentation only.

## Negative-constraint pass

negative-constraints (all honored):
- Never demote maintainer / refusal does not reset `project_limit` or call last-admin check: Task 2.
- Empty picture never clears: Tasks 2, 4.
- No migration, no `UserDB` change: Task 1 only parses the migration.
- No guard/settings/`UserManagementService`/login sync/authenticate change: no task edits them; the 2026-11-10 spec is untouched.
- Job must not import `codemie`: Task 4 keeps a local constant, verified by import-purity test.
- No new setting or response status for refusals: Task 2 logs only.
- No version-mismatch detection for picture: Task 6 documents only.
