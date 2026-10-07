# EPMCDME-15110 — Post-analysis fixes

Source: whole-story analysis of EPMCDME-15109 and its sub-tasks (2026-09-25), checked against the
Jira acceptance criteria. Each entry: finding → requirement → fix → proof.

## F1 (P1) Personal project could not be the default

- **Finding.** `set_default_project` / `clear_default_project` reused `_reject_if_personal_project`,
  so the user's personal project — a real membership row listed in the details panel — returned
  403/404. The UI offered the toggle and every click failed.
- **Requirement.** 15109/15112 AC1 "set **any** of the user's projects as the default"; open
  question "default must be one of the user's own projects".
- **Fix.** Guard removed for the default operations: the flag changes no membership. Choosing the
  personal project keeps unbound spend on the personal budget (consistent with EPMCDME-15111).
- **Proof.** `test_allows_personal_project_membership`.

## F2 (P2) Resolved project list was not deterministic

- **Finding.** The story asks for "the user's resolved project list deterministic"; the spec
  narrowed this and `get_by_user_id` / `aget_by_user_id` kept DB row order.
- **Fix.** Both queries `ORDER BY project_name`.
- **Proof.** `TestResolvedProjectListOrder` (sync + async).

## F3 (P2) Required tests were missing

- **Permission gate (AC4).** `TestDefaultProjectPermissionGate`: both routes carry
  `admin_or_maintainer_access_only`; the dependency admits admin/maintainer and rejects auditor,
  plain user and project admin (403).
- **DB constraint (AC1 backstop).** `TestOneDefaultConstraintDefinition` pins the partial unique
  index DDL. Enforcement verified on live dev Postgres: two `is_default=true` rows for one user →
  `duplicate key value violates unique constraint "uix_user_projects_one_default"` (transaction
  rolled back, nothing persisted).

## F4 (P2) Missed read model

- `PUT /v1/user/profile` (`user_profile_router.py`) built project rows without `is_default`, so the
  profile response always reported `false`. Fixed, and asserted in `test_user_profile_router.py`.
- To stop this class of omission, `is_default` is now **required** (no `= False` default) on
  `ProjectInfo`, `ProjectInfoResponse` and `AdminUserProject`.

## F5 (smells)

- `set_default_project` / `clear_default_project` duplicated ~30 lines each → one
  `_change_default_project()` helper.
- `except IntegrityError → 409 "changed concurrently"` caught every integrity error → only the
  `uix_user_projects_one_default` violation maps to 409; anything else re-raises
  (`test_unrelated_integrity_error_is_not_reported_as_concurrency`).
- Grant / update / revoke / set / clear now call `invalidate_user_from_cache(user_id)`, so AC11/AC12
  no longer depend on the 30 s auth-cache TTL (`test_invalidates_auth_cache_after_change`).
- Removed speculative `getattr(idp_user, "default_project", None)` in the enterprise IdP wrapper
  (EPMCDME-15111 CR-001) and its tests: no `codemie_enterprise` release carries the field, the
  default endpoints are unavailable without user management, and in user-management mode the User
  is rebuilt from the DB (see EPMCDME-15111 F1).

## Environment note

The dropped backfill migration (`d2c276d4390e`) had been applied to the local dev database during
EPMCDME-15111 QA, leaving `alembic_version` pointing at a revision that no longer exists. Dev DB
re-stamped to `bf3cb9db22b7`. No shared environment ever received it.

## F6 (P1, found in live pass 2) Swapping the default could fail with 409

- **Finding.** With real users, `PUT …/p2-b/default` while `p2-a` was the default returned
  `409 "Default project changed concurrently"` every time. `set_default` changed both rows and
  flushed once; the unit of work orders UPDATEs by primary key, so the new row's `is_default=true`
  could reach the DB before the old row's `false` and trip the one-default partial unique index.
  Pass 1 only passed because its row ids happened to flush in the safe order.
- **Fix.** `set_default` flushes the old row's reset before setting the new row
  (`user_project_repository.py`).
- **Proof.** `test_previous_default_is_flushed_before_target_is_set`; live: four consecutive
  a↔b swaps return 200 with exactly one default row.

## Live pass 2 (2026-09-26, real local auth, `ENV=development`)

Users registered through `/v1/local-auth` with roles admin / maintainer / auditor / plain user /
project admin (`is_project_admin` on `p2-a`), plus alice/bob/carol/dave as in pass 1.

| AC (15110) | Check | Result |
|---|---|---|
| 1 | set a→b→a→b; DB default rows | 200 each, exactly one row (after F6) |
| 2 | set default on non-member project / unknown project | 404, no row written |
| 3 | single removal (`DELETE …/projects/{p}`) and bulk removal (`DELETE /v1/projects/{p}/assignments`) of the default membership | default gone in both paths |
| 4 | set + clear as admin ✓, maintainer ✓, auditor ✗, plain user ✗, project admin ✗ | 403 for the three non-platform roles; auditor still reads `is_default` |
| F1 | personal project as default | 200 once the personal row exists (created on the user's first request) |

UI (Chrome, real sessions): admin — list markers with `role=img`/tooltip, details toggles, set-default
click → `PUT …/default` 200 and both rows flip, Add Project with "Set as default" → `POST …/projects`
then `PUT …/default`, table/DB/audit agree. Auditor — markers visible, every toggle disabled, no Add
Project / role select / Unassign.

Environment notes (not defects): registration is limited to 3/hour and login to 5/15 min per IP; the
backend image generates a fresh JWT keypair on every build (`.keys/` is not in the build context), so
every rebuild logs everyone out; UM-created users get their personal membership row on first request,
not at creation; `POST /v1/admin/users` requires maintainer (admin gets 403 from the service).

## Third-pass review (2026-09-26)

- **R03 — concurrent set requests can leave zero defaults (accepted).** Under READ COMMITTED,
  a set-current request that overlaps a replace-default request can reset the newer default from
  a stale target snapshot and return 200. Review fix: `set_default`/`clear_default` take
  `SELECT … FOR UPDATE` on the user row before reading membership flags, serialising default
  mutations per user; the old-row-first flush (F6) and the partial unique index remain. Added
  `TestDefaultMutationsSerializePerUser` (lock issued before any read, on both paths); live
  set/clear unchanged. Not reproduced live (needs controlled two-session interleaving).

Why my passes missed it: I tested sequential correctness and one flush-ordering race, never two
overlapping admin requests on the same user.
