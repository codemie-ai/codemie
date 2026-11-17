# EPMCDME-17239 Let every manager share a hosted app — Implementation Plan

**Goal:** For hosted apps, one named sharing rule (creator, project admin of the app's project, platform admin, maintainer) decides share, unshare, shared-with, people search, share-link get/create/revoke, single-grant delete and `can_share`. Assistants keep the owner rule.

**Architecture:** A SQL term `sharing_manager` plus its Python twin `is_sharing_manager` in `core/hosted_applications/access.py`. The catalog gate, the hosted grant descriptor's `is_owner`, the app branch of `may_remove` and `can_share` all read it. Rule symbols left without a consumer are removed (D9).

**Spec:** `spec.md` (same directory). **Tests:** follow `.ai-run/guides/testing/testing-patterns.md`. Run with `poetry run pytest <path> -q`. PG-only tests skip without `HOSTED_TEST_DATABASE_URL`.

Commit per task using the repository's existing convention.

## Acceptance criteria

The authoritative criteria are AC1–AC10 in `spec.md`. They are not copied here, so the two cannot drift. Where each one is covered:
- AC1 (each manager on every sharing operation, actor in the activity log): Task 3, Task 4
- AC2 (single-grant delete): Task 5
- AC3 (`can_share`): Task 7
- AC4 (refused personas): Task 3, Task 5
- AC5 (creator as recipient): Task 6
- AC6 (suspended app): Task 3
- AC7 (assistants unchanged): Task 2 and Task 4 change hosted code only; see the negative constraints below
- AC8 (managers message in refusals, route descriptions and baseline): Task 2, Task 3, Task 8
- AC9 (Python twin agrees with SQL): Task 1
- AC10 (design records): Task 9

---

## Task 1: Sharing rule: SQL term and Python twin
**Files:** `src/codemie/core/hosted_applications/access.py:121-246`; `tests/codemie/core/hosted_applications/test_access.py`, `tests/.../test_access_matrix.py` (PG)
Test-first: yes — a persona-table test where `is_sharing_manager` equals `sharing_manager` under SQL for creator, project admin of the app's project, admin of another project, platform admin, maintainer, member, recipient with READ grant, and outsider. It fails because the symbols do not exist yet. Add one case for a request user with `is_admin=True` whose `users` row is not admin: refused once passed through `platform_user`.
- Add `sharing_manager(apps, user_id_expr)`. It is `exists(_eligible_user)` AND (creator OR `_membership(admin_only=True)` OR `_platform_admin`), which is `_manager`'s terms without the grant branch. Refactor `_manager` to build on the same three-term list so `manageable` keeps its exact behaviour.
- Add `is_sharing_manager(created_by_id, project, user) = is_owned_by(...) or is_managed_by(project, user) or is_platform_admin(user)`. Its docstring says the input must be a `platform_user` copy.
- Do not touch `MANAGER_GRANT_ACTIONS` or `manageable` semantics (NG3).

## Task 2: Hosted grant descriptor reads the sharing rule; managers message has one home
**Files:** `src/codemie/service/hosted_applications/grant_descriptor.py:70-88`; `tests/codemie/service/hosted_applications/test_grant_descriptor*.py` (or the nearest descriptor test)
Test-first: yes — `DESCRIPTOR.is_owner(app, platform_project_admin)` is True, an outsider gets False, and `DESCRIPTOR.not_owner_message == MANAGERS_ONLY`. It fails today because `is_owner` is creator-only and the message is the generic default.
- Add the constant `MANAGERS_ONLY = "Only the app's managers (its creator, admins of its project, platform admins and maintainers) can share it or manage its access"`.
- `is_owner(app, actor)` calls `access.is_sharing_manager(creator_id, app.project, actor)`. Pass `not_owner_message=MANAGERS_ONLY` to `NamedGrantDescriptor`.
- Leave the generic `named_grant_types.NOT_OWNER` default and the assistant descriptor untouched (AC7).

## Task 3: Catalog gate admits every sharing manager; read paths take no lock
**Files:** `src/codemie/repository/hosted_applications/repository.py:110-133`; `src/codemie/service/hosted_applications/catalog.py:70-71,564-697`; `tests/codemie/service/hosted_applications/test_catalog.py` (:881, :917, :1063, :1105), `tests/codemie/rest_api/.../test_hosted_applications_router.py:615,685`
Test-first: yes — four tests, all failing today with 403 NOT_OWNER for the non-creator managers:
- `test_every_manager_shares_and_manages_links`: parametrized over creator, padmin (project admin), admin and maintainer. Each one succeeds on share, unshare, shared-with, sharable-users, link get, create and revoke, including on a grant or link that another manager created. The grant and link activity events carry that manager as actor.
- `test_non_managers_are_refused_on_every_sharing_operation`: parametrized over member, recipient (any grant level), marketplace viewer and admin of another project, on every operation in the first test. Each gets 403 with `MANAGERS_ONLY` when it can read the app; when it cannot (the other-project admin on an unshared app, or an outsider), it gets 404.
- `test_every_manager_on_a_suspended_app`: rewrites :1105. On a suspended app, each of the four managers succeeds on unshare, shared-with, link get and link revoke, and gets 409 APP_SUSPENDED on share and link create.
- `test_read_sharing_paths_take_no_lock` (RUN-1): shared-with, sharable-users and link get do not call `lock_quota`/`lock_app`.

Implementation:
- `managed_app` gains a predicate choice (for example `rule=manageable` defaulting to today's call, or a sibling `sharing_app(app_id, user, *, lock)`) so that the sharing gate filters by `access.sharing_manager`. Today's WRITE path stays unchanged.
- Replace `_owned_app` with `_sharing_app(tx, identity, app_id, *, lock)`. Keep the 403/404 split (`catalog.py:571-581`), with the 403 message read from `grant_descriptor.DESCRIPTOR.not_owner_message`. Delete the post-lock filter (`:582-587`), the `allow` and `refusal` parameters, and the constants `NOT_OWNER` and `NOT_LINK_REVOKER`.
- `shared_with`, `sharable_users` and `get_share_link` call it with `lock=False`. `share`, `unshare`, `create_share_link` and `revoke_share_link` keep `lock=True`.
- Update docstrings that say "owner only". Flip the existing owner-only assertions (:881, :917, :1063, router :615, :685) to the new rule.

## Task 4: Link revoke uses the sharing rule; drop `may_revoke`
**Files:** `src/codemie/service/hosted_applications/share_link_descriptor.py:23,47-50,78`; `src/codemie/service/permission/share_link_types.py:21,52-53`; `src/codemie/service/permission/share_link_service.py:196-207`; `tests/codemie/service/permission/test_share_link_service.py:271`
Test-first: yes — `arevoke_checked` by a project admin of the app's project succeeds, and a non-manager gets `access_denied(MANAGERS_ONLY)`. It fails today because `may_revoke` excludes project admins.
- Remove hosted `may_revoke`, the generic `ShareLinkDescriptor.may_revoke` field and its docstring line, and the `link_rules.may_revoke or` fallback. `arevoke_checked` then reads `grant.is_owner` only, with the descriptor's `not_owner_message`.
- Fix the `is_moderator` docstring (`access.py:232`) so it no longer mentions revoking share links.
- Rewrite the admin-revoke test at :271 as a manager-revoke test.

## Task 5: Single-grant delete: sharing rule for the app, admins for orphans; drop `grant_remover`
**Files:** `src/codemie/service/hosted_applications/grant_descriptor.py:23,29-43`; `src/codemie/core/hosted_applications/access.py:125-130,237-246`; `tests/codemie/core/hosted_applications/test_access.py:480-525`; `tests/.../test_share_links_pg.py:271` (PG)
Test-first: yes — `build_resolve_statement` gives `may_remove` True for a project admin of the app's project and False for a member. For an orphan grant (no app row) it is True for a platform admin or maintainer and False for a project admin. It fails today on the project-admin row.
- `may_remove = or_(and_(exists(_eligible_user(actor)), _platform_admin(actor), not exists(app row)), exists(select(apps.c.id).where(by_id, sharing_manager(apps, actor))))`. Export the two private terms only through small public helpers if needed.
- Delete `grant_remover`, `is_grant_remover` and their tests, including `test_is_grant_remover_agrees_with_the_sql_grant_remover`.
- The refusal stays `PermissionAccessDenied`, which the route answers with 401 "Access denied". Do not change `permission_deletion_service` or the router (NG6). Add a PG case where a project admin deletes successfully.

## Task 6: Creator as recipient: excluded from search, refused in share
**Files:** `src/codemie/service/permission/named_grant_service.py:764-775`; `src/codemie/repository/user_repository.py:660-690` (only if `condition` cannot carry it); `src/codemie/service/hosted_applications/catalog.py` (`share`, `sharable_users`); `tests/codemie/service/hosted_applications/test_catalog.py`, `tests/codemie/service/permission/test_named_grant_service.py`
Test-first: yes — (a) with 21 matching users, one of them the creator, `sharable_users` returns 20 users and never the creator. (b) A padmin's `share` with `[creator_id, other]` gets 400 "The app's creator already has access", and no grant rows or events are written. (c) The creator sharing with themselves is still dropped silently. Today (a) returns the creator and (b) inserts a row.
- `search_sharable_users(..., exclude_ids: Collection[str] = ())` ANDs `UserDB.id.not_in(exclude_ids)` into `condition`, so the exclusion applies before the SQL `LIMIT` (STR-6). Assistants pass nothing. Hosted passes `[app.created_by.id]`, and nothing excludes the caller (NG4).
- In `catalog.share`, after the locked gate and before `agrant`, if the actor is not the creator and `created_by.id in user_ids`, raise `HostedApplicationError("CREATOR_ALREADY_HAS_ACCESS", "The app's creator already has access", 400)`. The generic grant path is untouched.

## Task 7: `can_share` follows the sharing rule
**Files:** `src/codemie/service/hosted_applications/projections.py:54`; `tests/codemie/service/hosted_applications/test_projections.py:37-82`, `test_catalog.py:737`
Test-first: yes — the persona table expects `can_share` True for project_admin, platform_admin and maintainer (and for a suspended app), and False for member and recipient. It fails today for the three non-creator managers.
- Set `"can_share": access.is_sharing_manager(creator_id, app.project, user)` on the `platform_user` copy already passed in.

## Task 8: Route descriptions and OpenAPI baseline
**Files:** `src/codemie/rest_api/routers/hosted_applications.py:244-311`; `tests/codemie/rest_api/router_guards/baselines/openapi.json`
Test-first: no — replace the seven "Owner only" docstrings with wording that names the managers (the same sentence as `MANAGERS_ONLY`), then regenerate the baseline with `python -m tests.codemie.rest_api.router_guards.regenerate_baselines` and confirm `grep -c "Owner only"` finds none in either file.

## Task 9: Design records
**Files:** `docs/hosted-applications/architecture.md:533-540,590-620`; `docs/superpowers/specs/2026-11-06-EPMCDME-15659-hosted-apps-design.md` (:219-222, :286-290, :368-370, :471, :526, :666, :684-695, :1030, :1033, :1083, :1276-1277)
Test-first: no — state the sharing-manager rule in the roles and access tables, the §6.1 refusal, the API table and `can_share`. Mark D14, D17 and S-17 "Superseded by EPMCDME-17239" in place, and update the rejected-alternatives row.

---

## Negative constraints
- Assistants untouched (AC7): Task 2 overrides only the hosted message, Task 4 removes only the hosted-only link field, and Task 6's `exclude_ids` defaults to empty. The assistant descriptor and `_require_owner` are not edited by any task.
- No change to who counts as a manager (NG3): Task 1 adds a term and leaves `manageable` and `MANAGER_GRANT_ACTIONS` as they are.
- No backend actor exclusion in people search (NG4): Task 6 excludes the creator only.
- No migration and no removal of the creator's existing grants (NG5): no task touches data.
- `POST /v1/permissions` and the generic delete refusal are unchanged (NG6): Task 5 keeps `PermissionAccessDenied`, which the route answers with 401.
- No UI change, new levels, notifications or history (NG1, NG2): none planned.
- Sharing does not follow edit grants (Decision): Task 1's term has no grant branch.
