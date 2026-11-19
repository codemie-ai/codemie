# Technical Research

**Task**: idp-sync maintainer picture upsert
**Generated**: 2026-11-11
**Research path**: filesystem

---

## 1. Original Context

Восстановить две потерянные защиты старой Keycloak sync job в management API /v1/idp-sync/ (ветка EPMCDME-16895, API ещё не запушен, контракт можно менять обратно-совместимо; контекст — тикеты EPMCDME-16895 (API) и EPMCDME-16897 (переделка job), job потом уедет в репозиторий клиента, поэтому контракт должен быть финальным до этого).
Требования: (1) Maintainer не понижается: в upsert пользователя сервер НЕ должен снимать is_admin у пользователя с is_maintainer, даже если клиент прислал is_admin=false (в старой job: is_admin = профиль_Keycloak.is_admin OR db_user.is_maintainer, src/codemie/enterprise/sync/coordinator.py:152 в коммите 11b01c281, читать через `git show 11b01c281:src/codemie/enterprise/sync/coordinator.py`). Правило серверное, не зависит от клиента; подтвердить по коду семантику maintainer/admin в режиме IdP-Managed (в т.ч. коммит 4ee1894 «Accept is_admin together with Maintainer in IDP managed mode» и текущий UserManagementService, а также что is_maintainer и is_admin значат в UserDB, что делает project_limit, и как is_maintainer попадает в User/авторизацию).
(2) Синхронизация picture: необязательное поле picture в IdpUserUpsertRequest и в IdpUserResponse (src/codemie/rest_api/models/idp_sync.py), применение в сервисе src/codemie/service/user/idp_sync_service.py с идемпотентностью (то же значение — без записи), в job src/idp_sync_job/ — чтение атрибута picture из Keycloak (в старой job: attrs.get("picture", [""]) первый элемент), включение в diff и отправка в upsert; без изменения — ни одной записи.
Обязательно: тесты API и job, перегенерация baseline OpenAPI (tests/codemie/rest_api/router_guards/baselines/openapi.json, мержить только записи idp-sync вручную, если полная регенерация теряет роуты), обновление руководства docs/operations/idp-managed-sync.md (убрать эти две строки из таблицы «Safeguards of the old job that are lost»), обновление критериев AC в спеке API если нужно (docs/superpowers/tasks/2026-11-10-rbac-api-updates/spec.md — только описать, что поменять). Не менять: guard, IDP_SYNC_SERVICE_ACCOUNT_ID, IDP_MANAGED_ENABLED, блокировки режима, authenticate.
Research questions: where the maintainer rule goes (create vs update, reactivation, last-admin check ~line 194, admin-demotion rejection); can a maintainer be is_admin=False in DB today; how picture is stored/validated and what the existing user update flow does; how keycloak_source/state/reconcile/platform_client read and diff; Keycloak picture as attribute list; 'no picture' vs empty; backward compat of optional fields; test patterns.

---

## 2. Codebase Findings

### Existing Implementations

**Purpose A: a maintainer keeps is_admin**
- Old job: `git show 11b01c281:src/codemie/enterprise/sync/coordinator.py:149-156` `_profile_changes`: `is_admin = profile.is_admin or bool(db_user.is_maintainer)`; change only if differs from `db_user.is_admin`. Applied only when `profile.is_admin is not None`.
- `src/codemie/service/user/idp_sync_service.py:219-233` `upsert_user`: `target_admin = req.is_admin or (ADMIN_USER_ID and req.id == ADMIN_USER_ID)` (line 220). Computed BEFORE the user is loaded, so no maintainer knowledge. Create branch (222-228) -> `_create` (146-166) sets `is_admin=target_admin`, `project_limit=None if is_admin else config.USER_PROJECT_LIMIT`; never sets `is_maintainer` (new row = False, so create needs no maintainer rule). Update branch -> `_apply_update` (186-216).
- `_apply_update` 193-198: `reactivated = not is_active or deleted_at`; `if user.is_admin != target_admin:` -> if `user.is_admin and not reactivated` -> `_ensure_not_last_admin` (169-176, 409 "Cannot demote the last administrator", `lock_and_count_active_admins`); then `changes["is_admin"]` and `changes["project_limit"] = None if target_admin else USER_PROJECT_LIMIT`. Reactivation (199-201) drops memberships and sets active; it shares the same `target_admin` path, so one effective-admin value computed after loading the user covers update and reactivation. The last-admin check is skipped for reactivated users.
- `IdpSyncService.upsert_user` has no admin-demotion "rejection" besides the last-admin 409; the "rejection" of admin changes is `UserManagementService._reject_idp_admin_change` (`user_management_service.py:~720-745`) which belongs to the regular user-update flow, not idp-sync.
- Commit 4ee1894 (`user_management_service.py`): `_reject_idp_admin_change(session, user_id, requested_is_admin, requested_is_maintainer)` in IdP-managed mode: no-op when `IDP_MANAGED_ENABLED` is off or `requested_is_admin is None`; value equal to stored is allowed; `is_admin=True` for a user who is/becomes maintainer is allowed; else 403 "The Admin role is controlled by the identity provider". Docstring: "Maintainer and Auditor stay editable: they are platform roles." Tests: `tests/codemie/service/user/test_user_management_service_idp_admin_guard.py`.
- Invariant "maintainer implies admin": `UserManagementService.create_local_user`-style creation line 112-113 (`if is_maintainer: is_admin = True`); `_normalize_role_updates` 709-716 (is_maintainer -> is_admin True; `is_admin False` + maintainer -> 400 "Maintainers must also be admins"); `User` model validator `security/user.py:~100` (`if self.is_maintainer: self.is_admin = True`); bootstrap user `authentication_service.py:~671-672` and `user_management_service.py:451-452` created with both True.
- Meaning: `UserDB.is_admin` (`rest_api/models/user_management.py:51`, indexed) = admin; `is_maintainer` (:52) = platform role above admin, only maintainers may change admin/maintainer roles (`user_management_service.py:765`); `project_limit` (:57) = max shared projects, NULL = unlimited, auto-managed to NULL for admins and `USER_PROJECT_LIMIT` otherwise (`_auto_manage_project_limit` ~776-795; same in idp_sync_service 161, 198). `is_maintainer` reaches the authorization `User` via `authentication_service.py:173, 375, 759` (`is_maintainer=db_user.is_maintainer`), and `User` forces `is_admin=True`.
- Can a maintainer be `is_admin=False` in DB today: all code paths write both True/forbid False (above), so not via the UI/API. BUT the idp-sync upsert (`_apply_update`) with `is_admin=false` is exactly the path that can set it (the gap this task closes): it writes `is_admin=False` for any existing user with `is_maintainer=True`, and `project_limit` to the limit. Legacy/manual rows are not excluded by DB constraint (no check constraint found in the model). The runtime `User` validator still masks it for authorization.
- `IdpUserResponse` has no `is_maintainer` (`models/idp_sync.py:65-72`); `_to_response` (`idp_sync_service.py:88-102`) reports `user.is_admin`.

**Purpose B: picture sync**
- `UserDB.picture: Optional[str] = SQLField(default=None)` (`user_management.py:48`) - plain string, no length/URL validation, no max size in the model. Request/response DTOs there carry `picture: Optional[str] = None` (:225, 249, 269) with no validators.
- Existing update flow: `user_management_service.py:847-848` `if picture is not None: updates["picture"] = picture` (None = leave, no clear). Login sync: `authentication_service.py:318-319` `if db_user.picture != idp_user.picture: updates["picture"] = ...` (profile mirror per login; `User.picture: str = ""`; DB->User uses `db_user.picture or ""`, :165, 368). Create-from-IdP at :208.
- Neither `IdpUserUpsertRequest` nor `IdpUserResponse` nor `_create`/`_apply_update` handle picture today; `_apply_update` emits event attributes only for `{email, username, name, is_admin}` (213).
- Old job: `_KcProfile.picture: str`, `pictures = attrs.get("picture", [""])`, `picture=pictures[0] if pictures else ""` (old coordinator:123-128), mirrored with plain `!=` against `db_user.picture` (so DB `None` vs `""` always differed -> a write every run for users without picture), create passed `picture=profile.picture` (old :303).

**Job (`src/idp_sync_job/`)**
- `keycloak_source.py`: `KeycloakUser` dataclass (55-66: id, email, username, name, is_admin, memberships); `_to_user` (261-278) builds it, `attrs = raw.get("attributes")` dict-or-empty; attribute values are read as lists (`attrs.get("roles", [])` line 245, `_split_csv(attrs.get("applications"))`), i.e. Keycloak returns user attributes as `{name: [values]}`. `_fetch_users` uses full representation (attributes included).
- `state.py`: `UserState` (frozen dataclass) `from_keycloak` / `from_platform` (`name=user.name or ""`), `profile_differs` compares `(email, username, name, is_admin)`, `upsert_payload` returns the 5 keys. `picture` absent everywhere.
- `platform_client.py`: `PlatformUser` (id, is_active, email, username, name, is_admin, memberships); `_to_user` reads response items with `.get`; `upsert_user(payload)` PUTs the payload as JSON unchanged.
- `reconcile.py`: `plan_users` - CREATE/REACTIVATE always carry `payload=desired.upsert_payload()`; UPDATE only when `desired.profile_differs(current)`; `admin_change` flag drives promotion/demotion ordering (flip only on `is_admin` change). Reactivation sends payload regardless of diff.
- Note: the job compares `desired.is_admin` (Keycloak) to the platform `is_admin`; a maintainer with Keycloak `is_admin=False` produces a "demotion" UPDATE each run if the server returns True after refusing to demote (server keeps True, job sees `True != False` again every run, an endless no-op PUT unless the job or server handles it). Speculative: the diff may need to treat this.

### Decisions Already in the Code
- Admin flag is advisory client input, applied by the server; server adds `ADMIN_USER_ID` override (`idp_sync_service.py:220`; spec AC6).
- Maintainer is a platform role not owned by the IdP (4ee1894 docstring; old job comment "a platform role the sync does not own").
- Idempotency convention: compare field by field and write nothing when `changes` is empty (`_apply_update` 202-203, test `test_upsert_without_changes_is_a_noop`); events only when something changed.
- Optional fields in `None`-means-leave style in the user update flow (`picture is not None`).
- Response models have no `extra=` config: pydantic default ignores unknown request fields; the job's `_to_user` uses `.get`, so it tolerates new response fields and missing ones.
- Stored in `UserDB.picture` nullable, read as `db_user.picture or ""` elsewhere.

### Task Statements Checked Against the Code
- "old: is_admin = KC OR db_user.is_maintainer, coordinator.py:152": holds at lines 152-153 of `11b01c281` (comment line 152, expression 153).
- "is_maintainer absent from the API response": holds (`IdpUserResponse` has no field); the guide row says the same.
- "old: attrs.get('picture', ['']) first element": holds (old coordinator:123, 128).
- "no-change means no writes at all": holds for existing fields; for picture the old job did NOT satisfy it for None vs "" (see above).
- "contract can change backward-compatibly, API not pushed": unverified from git (no remote check performed).
- "full OpenAPI regeneration may lose routes": unverified; `tests/codemie/rest_api/router_guards/regenerate_baselines.py` exists; spec line 34 says regenerate via it. idp-sync entries at `openapi.json` lines 60704, 60773, 60891.

### Consumers of Changed Contracts
- `IdpUserUpsertRequest`: router (`rest_api/routers/idp_sync.py`), `idp_sync_service.upsert_user`, tests `tests/codemie/rest_api/models/test_idp_sync_models.py`, `.../routers/test_idp_sync_router.py`, `tests/codemie/service/user/test_idp_sync_service.py` (`_req` helper), OpenAPI baseline (`tests/codemie/rest_api/router_guards/baselines/openapi.json`, snapshot test `test_openapi_snapshot.py`).
- `IdpUserResponse`/`IdpUserPage`: `_to_response`, job `platform_client._to_user` (tolerant), `tests/idp_sync_job/platform_fake.py:42` (fake item dict), `test_platform_client.py:64`, `tests/performance/test_idp_sync_list_load.py`.
- Job: `KeycloakUser` constructed in `tests/idp_sync_job/test_reconcile.py` helper `kc()`, `PlatformUser` in `pf()`; `UserState` used by reconcile/coordinator tests; payload shape asserted in `test_platform_client.py:175-187`.
- Docs: `docs/operations/idp-managed-sync.md:256-263`; spec `docs/superpowers/tasks/2026-11-10-rbac-api-updates/spec.md` lines 21 (upsert behaviour), 34, 42 (AC6), 52 (AC16), 72/86 (is_admin trust).

### Architecture and Layers Affected
Pydantic API models -> `IdpSyncService` (service) -> `user_repository`/`UserDB`; router unchanged by contract; separate job package `idp_sync_job` (keycloak_source -> state -> reconcile -> coordinator -> platform_client) that must not import `codemie` (see `tests/idp_sync_job/test_import_purity.py`).

### Integration Points
Job -> `PUT /v1/idp-sync/users/{id}` and `GET /v1/idp-sync/users`; Keycloak admin REST. Activity events via `activity_event_repository`.

### Patterns and Conventions
Static-method service, caller-owned session, savepoints for writes, `changes` dict then single flush; job uses frozen dataclasses and pure diff; tests use a SQLite in-memory `session` fixture with patched `lock_and_count_active_admins` and `events` mock.

---

## 3. Documentation Findings

### Guides and Architecture Docs
`.ai-run/guides/integration/` has no idp-sync guide; operating guide `docs/operations/idp-managed-sync.md` (table "Safeguards of the old job that are lost", lines 256-263; the two target rows at 260-261).

### Architectural Decisions
4ee1894 commit message: a maintainer is always an admin. Spec AC6/AC16 and line 21 describe upsert behaviour.

### Derived Conventions
Contract documented via Field descriptions/examples (spec AC16, test `test_is_admin_is_described_as_advisory` checks description text).

---

## 4. Testing Landscape

### Existing Coverage
- API/service: `test_idp_sync_service.py` (upsert create/update/noop/ADMIN_USER_ID/last admin/parallel/reactivation/race/conflicts; helpers `_req`, `_seed_user`, `_event_types`), `test_idp_sync_router.py`, `test_idp_sync_models.py`, guard tests, baseline tests.
- Job: `tests/idp_sync_job/` test_keycloak_source, test_reconcile (`kc()`, `pf()` helpers), test_platform_client, test_coordinator, fakes `keycloak_fake.py` (`FakeKeycloak`, `make_settings`) and `platform_fake.py`.

### Testing Framework and Patterns
pytest (async tests unmarked in job, httpx.MockTransport fakes), in-memory SQLite with JSON compiler shim, `patch.object(user_repository, "lock_and_count_active_admins", ...)`.

### Coverage Gaps
No test of a maintainer under upsert; no picture anywhere in idp-sync tests (`grep picture tests/idp_sync_job` empty).

---

## 5. Configuration and Environment

### Environment Variables
`ADMIN_USER_ID`, `USER_PROJECT_LIMIT`, `IDP_PROVIDER` used by the service; job settings in `src/idp_sync_job/settings.py` (`admin_user_id`, `deactivate*`). No new vars found necessary by the code. `IDP_MANAGED_ENABLED`, `IDP_SYNC_SERVICE_ACCOUNT_ID` are out of scope per task.

### Configuration Files
OpenAPI baseline `tests/codemie/rest_api/router_guards/baselines/openapi.json` (+ `regenerate_baselines.py`).

### Feature Flags and Deployment Concerns
None for these changes; job may be deployed against an older or newer API (see risks).

---

## 6. Risk Indicators

- Speculative: the effective-admin value must be computed after `get_any_by_id` (currently line 220 runs before the load); both update and reactivation read `target_admin`, create does not need the rule.
- Speculative: with the server keeping a maintainer admin, the job diff (`profile_differs` on `is_admin`) sees `False != True` on every run and re-sends an UPDATE (and may classify it as a demotion) -- idempotent on the server but not "no writes" for the API calls; the job cannot know `is_maintainer` since the response lacks it. See Existing Implementations (Job).
- Speculative: the response `is_admin` stays True for the maintainer, so a job built before this change is unaffected by a response change; adding `picture` to the response is additive, `platform_client._to_user` uses `.get`.
- Picture: no length/URL validation exists anywhere in `UserDB`, DTOs, or login sync; a data URI or large string would be accepted by the DB TEXT/str column (column type not confirmed - no migration inspected). Keycloak picture may be absent/empty; DB null vs "" mismatch can cause repeated writes unless normalized (the old job had that flaw).
- Job built against an older contract: unknown request fields are ignored by pydantic default (no `extra=forbid` found), so a new job against an old API silently drops picture; an old job against the new API omits picture, so the "omitted vs empty" semantics matter (user update flow uses None = leave alone).
- Last-admin check and `project_limit` update sit in the same branch as the maintainer rule: a refused demotion must also not reset `project_limit` to the limit.
- OpenAPI baseline: hand-merge of three idp-sync paths plus the changed schemas.
- Spec/doc drift: spec line 21 and AC list, ops guide table rows.

---

## 7. Summary for Complexity Assessment

The change touches the pydantic models, one service module (`idp_sync_service.py`, upsert/update/reactivation/response mapping), the separate `idp_sync_job` package (keycloak_source, state, reconcile, platform_client payload), the OpenAPI baseline, one ops guide table and the API spec ACs. File surface is about 6 source files plus 7-8 test files and 3 docs; no schema or migration is needed for picture or maintainer as found (columns already exist on `UserDB`).

Technical novelty is low: both behaviours existed in the old job (`11b01c281`) and the invariant "maintainer implies admin" is already enforced in several places (4ee1894, `_normalize_role_updates`, `User` validator). The one subtle area is the job's diff when the server refuses to demote a maintainer, and None/"" picture normalization.

Test posture is good for the service (rich `test_idp_sync_service.py`) and job (fakes and pure reconcile tests), with no existing maintainer or picture cases. Main risks are cross-version compatibility of the job and API, the diff loop for maintainers, and the baseline merge.

---

## 8. External References

None named by the task (git commit `11b01c281` and `4ee1894` are in this repository and were read via `/usr/bin/git show`).
