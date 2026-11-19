# Spec: restore maintainer and picture safeguards in /v1/idp-sync (EPMCDME-16895, EPMCDME-16897)

## Goal
Bring back two protections of the old Keycloak sync job (`11b01c281:src/codemie/enterprise/sync/coordinator.py:149-156`, picture at :123-128) in the management API `/v1/idp-sync/` and in `src/idp_sync_job/`. The API is not pushed yet, and the job moves to the client's repository, so the contract is finalised here. All contract changes are additive.

## Behaviour
1. **Maintainer is never demoted (server).** In `IdpSyncService.upsert_user` (`idp_sync_service.py:219-233`, `_apply_update` 186-216) the effective admin is computed in ONE private helper of `IdpSyncService`: `req.is_admin OR ADMIN_USER_ID match OR user.is_maintainer`. It runs after the user is loaded and its result feeds update and reactivation. Create is unchanged (a new row has `is_maintainer=False`).
   - When the rule overrides the request, `is_admin` and `project_limit` are not touched and `_ensure_not_last_admin` is not called.
   - A refusal is logged only when the request says `is_admin=false` and the stored row is `is_admin=true` for a maintainer.
   - A legacy maintainer row with `is_admin=False`: the effective value is True, so the existing diff heals it (and the log is not written). Covered by a test.
2. **`is_maintainer` in the response.** `IdpUserResponse` gets a read-only `is_maintainer: bool` (additive).
3. **Job: effective admin computed once.** `UserState.from_keycloak(kc_user, platform_user)` (`state.py`) is the only place that computes `kc.is_admin OR platform.is_maintainer`. `PlatformUser` (`platform_client.py`) gets `is_maintainer: bool | None` (None when the response lacks the field). `reconcile.py:129-146` keeps working unchanged on the resulting `is_admin`: `admin_change` is raised only when the effective value differs from the platform's, so a maintainer never produces a demotion or a repeated PUT.
   - **Old API** (`is_maintainer is None`): the job never sends a true-to-false `is_admin` change for that user, keeps the platform value, and logs one warning per such user per run with the key `maintainer_unknown`, counted in the run report as `demotions_skipped_unknown_maintainer`. Other changes are sent normally.
4. **Picture.** `IdpUserUpsertRequest.picture` and `IdpUserResponse.picture` are optional strings.
   - Omitted, `None` and `""` mean "leave alone": an empty value never clears an existing picture. Clearing goes through the regular user-update flow.
   - A non-empty value is written only if it differs from the stored one (stored `None` and `""` are the same). Create stores a non-empty picture. The activity event lists `picture` only on a real change.
   - Limit: `PICTURE_MAX_LENGTH = 1024` (constant in `models/idp_sync.py`), equal to the column `users.picture = sa.String(1024)` (`src/external/alembic/versions/299a10eb05a0_add_user_management_tables.py:39`). Exactly 1024 is accepted, 1025 gives 422. No migration.
5. **Job picture.** `keycloak_source` reads the first element of the `picture` attribute list (absent or empty gives `""`). `UserState` carries it; `profile_differs` and `upsert_payload` include it, and an empty picture is never a difference and never in the payload. A picture longer than 1024 is dropped from the payload with a warning and the rest of the user syncs normally. The job holds its own copy of the 1024 limit (it must not import `codemie`).
6. **Cross-version.** Old job against new API: omits picture, safe. New job against old API: picture is silently lost (pydantic ignores unknown fields), documented as a known limitation, no detection. Maintainer safety against an old API is handled by point 3.

## Acceptance criteria
- AC1: upsert with `is_admin=false` for a maintainer keeps `is_admin=True` and `project_limit`, no last-admin 409.
- AC2: the same for reactivation of a maintainer.
- AC3: a non-maintainer is demoted as before, including the last-admin check.
- AC4: `ADMIN_USER_ID` and create behaviour are unchanged.
- AC5: picture: a new value is written; the same value gives no write and no event; omitted, `None` and `""` keep the stored value; create stores it.
- AC6: picture of 1024 characters is accepted, 1025 gives 422. A test asserts `PICTURE_MAX_LENGTH` equals the length declared for `picture` in migration `299a10eb05a0` (parsed from its source). The 1025 boundary is tested at the API model level, because SQLite does not enforce VARCHAR length and would mask the problem.
- AC7: `IdpUserResponse` returns `picture` and `is_maintainer`, both described in Field descriptions.
- AC8: the job reads the Keycloak `picture`, includes it in the diff and payload, makes zero calls when nothing changed, and does not re-send for a maintainer with Keycloak `is_admin=false`.
- AC9: the job drops a picture over 1024 with a warning and syncs the rest.
- AC10: against a response without `is_maintainer`, the job sends no `is_admin` true-to-false change, logs `maintainer_unknown` once per user per run and counts it; a test covers this.
- AC11: a refusal log is written only for request false and stored true; a legacy maintainer row with `is_admin=False` is healed to True (test).
- AC12: the OpenAPI baseline `tests/codemie/rest_api/router_guards/baselines/openapi.json` is updated, merging only the idp-sync paths (~60704, 60773, 60891) and schemas by hand if full regeneration loses routes. The snapshot test passes.
- AC13: `docs/operations/idp-managed-sync.md`: remove the two rows (260-261) from "Safeguards of the old job that are lost" and describe: the new rules; rollout order "API first, then job"; the picture cross-version loss; the maintainer-grant race (unchecked read, self-corrects on the next run); picture sources in managed mode: login sync (`authentication_service.py:318`, mirrors with `!=` and can erase) and the job both write, so the last writer wins, and the job restores the picture on its next run if Keycloak has one.
- AC14: API and job tests added (`test_idp_sync_service.py`, `test_idp_sync_models.py`, `test_idp_sync_router.py`, `tests/idp_sync_job/*`, fakes); `test_import_purity.py` passes.
- AC15: the description of the AC changes for the 2026-11-10 spec below is delivered as part of this spec; that file is not edited.

## Proposed changes to the 2026-11-10 spec (description only)
Line 21 (upsert behaviour): add the maintainer rule and picture sync. AC6: add "a maintainer keeps admin". AC16 (contract descriptions): add `picture` and `is_maintainer`. Lines 72 and 86 (is_admin trust): the server overrides `is_admin=false` for maintainers.

## Decisions
- Maintainer rule on the server over a client-side rule: the client cannot be trusted and the job moves out of the repo.
- Effective admin computed after loading the user over the current pre-load computation: it needs `is_maintainer`.
- Server formula in one private `IdpSyncService` helper over inline copies in update and reactivation.
- Job formula in `UserState.from_keycloak(..., platform_user)` only over a second copy in reconcile.
- Rule on update and reactivation, create unchanged over a rule on create too: a new row is never a maintainer.
- Refused demotion leaves `is_admin` and `project_limit` untouched over resetting `project_limit`.
- Refusal logged only (request false, stored true) over a counter or response flag on the server.
- Read-only `is_maintainer` in the response over accepting a repeated idempotent PUT — user.
- Old API without `is_maintainer`: job never sends a demotion and warns, over trusting the Keycloak value.
- Empty or omitted picture is ignored over clearing the platform picture — user.
- `None` and `""` normalised to one value over plain `!=` as in the old job.
- Picture limit 1024, any format, equal to the column size, over 2048, URL-only or a large limit — user, answers AR-1.
- No migration over widening the column.
- Over-long picture dropped with a warning in the job over failing the user.
- Picture loss against an old API documented, not detected, over a version handshake.
- Additive optional fields over a new endpoint or version.

## Non-goals
- No change to the guard, `IDP_SYNC_SERVICE_ACCOUNT_USERNAME`, `IDP_MANAGED_ENABLED`, mode locks or `authenticate`.
- No change to `UserManagementService` (`_reject_idp_admin_change`) or the login sync.
- No picture clearing through sync, no URL or data-URI validation, no DB migration, no change to `UserDB`.
- No new setting or response status for refused demotions.
- No edit of the 2026-11-10 spec file.
- No detection of API/job version mismatch for picture.
