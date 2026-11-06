# Assistant Sharing Backend Review Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use `- [ ]` tracking.

**Goal:** Make named assistant sharing safe to deploy, gated, audited and correct (review items a1, a3, a5, a7, a8, a9, a13-a18).
**Architecture:** All grant mutations funnel through `AssistantShareService`, which writes a fail-closed audit event in the same DB transaction. Migration only adds the `ASSISTANT` label.
**Tech Stack:** FastAPI, SQLModel/SQLAlchemy, Alembic (`alembic_postgresql_enum`), pytest.
**Spec:** `docs/superpowers/tasks/2026-10-08-epmcdme-15748-backend-review-fixes/spec.md` (research: `technical-analysis.md` in the same dir).

Commit per task using the repository's existing convention (`EPMCDME-15748` / branch ticket; see `.ai-run/guides/standards/git-workflow.md`). Run only the task's own tests; the whole-suite gates run later in the flow.

## Global Constraints

- Do not touch `.codemie/codemie-cli.config.json`. The working tree has uncommitted edits to the migration file; Task 1 edits that file in place, keeping its on-disk `revision`/`down_revision`, and creates no new revision.
- Existing fail-open `ActivityRepository.insert` and its callers stay unchanged.
- No project-member restriction on share targets (a4), no auto-unsetting of integrations, no frontend work.

## Review Focus

- Grant request with the same user id twice, and two concurrent identical grants: success, one row, one event.
- Mixed valid/invalid ids: nobody is granted, no event written.
- Revoke of a user with no grant, re-grant of an existing grant: no event.
- `ACTIVITY_EVENTS_ENABLED` off: mutation succeeds, no event. On and insert raises: mutation rolled back.
- Recipient converted to external user: no access via `is_shared_with`, list, or count.

---

### Task 1: Additive ASSISTANT migration and enum mapping (a1)

**Files:** Modify `src/external/alembic/versions/a1b2c3d4e5f8_add_assistant_to_resourcetype.py:34-92`, `src/codemie/rest_api/models/permission.py:74-94`. Test: `tests/codemie/rest_api/models/test_permission_migration.py` (new).
**Test-first:** yes - a test patches `op.sync_enum_values`/`op.execute` in the migration module and asserts upgrade makes one call (`resourcetype`, new_values `["DATASOURCE","ASSISTANT"]`, no `enum_values_to_rename`) and none for principaltype/action; downgrade executes the delete of `'ASSISTANT'` rows before the single `sync_enum_values(new_values=["DATASOURCE"])`; a second test asserts the three `Permission` enum columns have no `values_callable` (persist names).

- [ ] Write the failing tests; run `pytest tests/codemie/rest_api/models/test_permission_migration.py -q` (FAIL).
- [ ] Rewrite upgrade/downgrade as above (delete filter value is uppercase `'ASSISTANT'`; drop all lowercase relabels). Remove the three `values_callable` args in `permission.py`; keep enum members and `PermissionCreateRequest` untouched.
- [ ] Run the test plus `tests/codemie/rest_api` permission/datasource permission tests that exist (grep `ResourceType.DATASOURCE`); PASS.

### Task 2: Strict audit insert and assistant event constants (a7 foundation)

**Files:** Modify `src/codemie/service/activity/activity_models.py` (constants next to existing domain/event constants), `src/codemie/service/activity/activity_repository.py` (abstract base near L122 and `SQLActivityEventRepository` after `insert` L138). Test: `tests/codemie/service/activity/test_activity_repository.py`.
**Test-first:** yes - tests: `insert_strict` returns None and adds nothing when `ACTIVITY_EVENTS_ENABLED` is False; adds the row via `session.add`+`flush` in the caller's transaction when True; re-raises when `flush` raises (existing `insert` still swallows).
**Produces:** constants `DOMAIN_ASSISTANT="assistant"`, `EVENT_ASSISTANT_GRANT_ADDED`, `EVENT_ASSISTANT_GRANT_REMOVED` (follow existing naming style); `SQLActivityEventRepository.insert_strict(event: ActivityEventCreate, session: Session) -> ActivityEvent | None`.

- [ ] Write failing tests; implement (no `begin_nested`, no try/except); tests PASS.

### Task 3: Share service - public authorized entry points, audit, idempotent grant, guard, search cap (a5-grant, a7, a13, a16, a3-search)

**Files:** Modify `src/codemie/service/assistant/assistant_share_service.py:34-152`. Test: `tests/codemie/service/assistant/test_assistant_share_service.py`.
**Consumes:** `insert_strict`, constants (Task 2).
**Produces:** `grant(owner, assistant, user_ids) -> list[dict]`, `revoke(owner, assistant, user_ids)`, `list_for_owner(owner, assistant)`, `search_sharable_users_for_owner(owner, assistant, query)` - each runs the owner check (403) itself; `revoke_permission(actor, permission)` (no owner check; caller authorizes; used by Tasks 6); `remove_all_grants(actor, assistant_id)` (Task 7); `assistant_uses_personal_integration(assistant_like) -> bool`; `has_named_grants(assistant_id) -> bool`. `_require_owner` stays private.
**Test-first:** yes - failing tests: (1) duplicate `user_ids` yield one row and one event; (2) one invalid id -> 4xx and zero rows/events; (3) repeat grant writes no event; (4) pre-existing row skipped without error (conflict-safe path); (5) `insert_strict` raising leaves no rows (rollback) and surfaces an error; (6) revoke writes one event per deleted row, none for absent grants; (7) grant on an assistant with a USER-type integration -> 400 with descriptive message naming personal integrations, no rows; (8) `search_sharable_users_for_owner` returns at most 20; (9) non-owner gets 403 on all four public methods.

- [ ] Write failing tests.
- [ ] Grant: validate all ids first (reuse the existing 404/400 checks), dedupe preserving order, then one `postgresql.insert(Permission).on_conflict_do_nothing(constraint="unique_resource_principal").returning(Permission.id)` per id in a single `Session` transaction; write one `insert_strict` event only for ids that returned a row; commit once; any exception rolls back.
- [ ] Revoke: `delete ... returning` per principal in one transaction, event per deleted row. Both paths share one private `_audit(session, actor, assistant_id, project, principal_id, before, after)`.
- [ ] Event: `entity_type="assistant"`, `entity_id=assistant id`, `actor_id=actor.id`, `attributes={actor_id, actor_username, principal_type, principal_id, assistant_id, project, before, after, occurred_at (UTC ISO)}`; `before/after` hold the permission level or null.
- [ ] Personal-integration check: collect settings ids from the assistant's toolkits, their tools and MCP servers (`rest_api/models/assistant.py:144-190`), load each stored `Settings` (`rest_api/models/settings.py:264`) and test `setting_type == SettingType.USER`; the request-payload default is USER, so never trust it. Grant raises `ExtendedHTTPException(400)` before any write.
- [ ] Search: slice the `aquery_active_users` result to 20 (pass a limit if the repository supports one).
- [ ] Run `pytest tests/codemie/service/assistant/test_assistant_share_service.py -q`; PASS.

### Task 4: Router - gate, 404, public entry points (a3, a15, a16)

**Files:** Modify `src/codemie/rest_api/routers/assistant.py:3318-3352`. Test: `tests/codemie/rest_api/routers/test_assistant_share.py`, `tests/unit/routers/test_assistant_sharing_security.py`.
**Test-first:** yes - failing tests: each of the four routes returns 400 "User management not enabled" when `config.ENABLE_USER_MANAGEMENT` is False and calls nothing; unknown assistant id returns 404 on all four (including DELETE share, which has no test today); `q` of length 1 on `sharable-users` returns 422; routes call the service public methods (no `_require_owner` reference left in the router).

- [ ] Write failing tests. Add a small `_require_user_management()` in the router (import `_USER_MGMT_NOT_ENABLED` from `user_management_router.py:57`, raise `ExtendedHTTPException(code=400, ...)` as at its L144) and call it first in each route.
- [ ] Replace `Assistant.get_by_id` with `_get_assistant_by_id_or_raise` (L2093); replace `_require_owner`+service calls with `list_for_owner` / `search_sharable_users_for_owner`; set `Query(default="", min_length=2)`.
- [ ] Run both test files; PASS.

### Task 5: Personal-integration guard on assistant update (a5)

**Files:** Modify `src/codemie/rest_api/routers/assistant.py:909-` (`update_assistant`, after the access check at L918). Test: `tests/codemie/rest_api/routers/test_assistant_share.py`.
**Consumes:** `has_named_grants`, `assistant_uses_personal_integration` (Task 3), applied to the incoming `AssistantRequest`.
**Test-first:** yes - failing test: updating an assistant that has a named grant, with a request adding a USER-type integration, returns 400 with a descriptive message and the assistant is unchanged; same request with no grants, or with only project-type integrations, succeeds. Create needs no guard (a new assistant has no grants); add one assertion that create is unaffected.

- [ ] Write failing tests; add the guard before any persistence; PASS.

### Task 6: Generic permission delete delegates for ASSISTANT rows (a17)

**Files:** Modify `src/codemie/service/permission/permission_deletion_service.py:25-31`, `src/codemie/rest_api/routers/permission.py:76` (message "create permissions for" -> "delete permissions for"). Test: `tests/codemie/service/permission/test_permission_deletion_service.py` (new), plus a route test for `DELETE /v1/permissions/{id}`.
**Consumes:** `revoke_permission` (Task 3).
**Test-first:** yes - failing tests: grantor deletes an ASSISTANT row -> row gone and one audit event; platform admin likewise; any other user -> `PermissionAccessDenied`, row kept; DATASOURCE row still goes through `permission.delete()` untouched.

- [ ] Write failing tests. Keep `Ability(user).can(Action.WRITE, permission)` as the grantor-or-admin gate (confirm in the admin test; if it fails add `user.is_admin_or_maintainer`), then branch on `permission.resource_type == ResourceType.ASSISTANT` to call `AssistantShareService.revoke_permission`. Tests PASS.

### Task 7: Assistant delete removes grants with audit (a18)

**Files:** Modify `src/codemie/rest_api/models/assistant.py:1145-` (`delete_assistant`: add optional `user: User | None = None`, call `AssistantShareService.remove_all_grants` before deleting the assistant; grep callers of `delete_assistant` and pass the acting user). Test: `tests/codemie/rest_api/models/test_assistant_delete_grants.py` (new).
**Test-first:** yes - failing test: deleting an assistant with two grants leaves zero ASSISTANT rows for it, writes two removal events, and a pre-existing event for that assistant remains; a failing audit insert aborts the assistant delete.

- [ ] Write failing test; implement `remove_all_grants(actor, assistant_id)` (select rows, delete, one event each, one transaction) and wire it; PASS.

### Task 8: Shared named-grant predicate, external-user skip, one-query grant preload (a8, a9, a14)

**Files:** Modify `src/codemie/rest_api/models/permission.py` (add `Permission.named_grant_resource_ids(user) -> Select | None`, `None` for `user.is_external_user`; selects `cast(resource_id, Text)` filtered to ASSISTANT/USER/`user.id`; do not use `Permission.get_for`), `src/codemie/rest_api/models/assistant.py:871-884` (`is_shared_with` gains `granted_ids: set[str] | None = None`; use helper, False when helper is None), `src/codemie/service/assistant/assistant_repository.py:593-604` and `:687-698` (use helper; `false()` when None), list loop `:216-217`, `src/codemie/core/ability.py:162-198` (optional `granted_ids` passed to `Assistant.is_shared_with` only). Test: `tests/codemie/core/test_ability.py`, `tests/unit/routers/test_assistant_sharing_security.py`, repository tests.
**Test-first:** yes - failing tests: external user with a named grant gets `is_shared_with` False and the compiled list/count predicates contain no grant subquery; an internal user keeps access; listing a page of N assistants issues exactly one Permission query (count executed statements) and `user_abilities` still include READ for granted ones.

- [ ] Write failing tests.
- [ ] Add helper; replace the three copies. In the list loop, run one query `helper.where(cast(Permission.resource_id, Text).in_(page_ids))` into a set and pass it via `Ability(user, granted_ids=...)`; `granted_ids is None` keeps the old per-object lookup for other callers.
- [ ] Run the three test files; PASS.

---

## Negative-constraint check

Honored: no lowercase relabel or `values_callable` (T1); existing `insert` untouched, no retention/purge change (T2); no target restriction a4 (T3); reject-only, no auto-unset, project share untouched (T3, T5); `Permission.get_for` unused (T8); no frontend, no unrelated refactor (only the one-word delete message in T6). Spec coverage: a1 T1, a3 T3/T4, a5 T3/T5, a7 T2/T3/T6/T7, a8/a9/a14 T8, a13 T3, a15/a16 T4, a17 T6, a18 T7.
