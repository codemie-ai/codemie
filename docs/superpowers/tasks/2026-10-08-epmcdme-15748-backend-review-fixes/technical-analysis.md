# Technical Research — Backend slice (codemie)

**Sliced from**: ../../technical-analysis.md (codegraph, 2026-10-08). Full review evidence: ../../review-input.md (line numbers at BE HEAD `74e709a10`; router shifted ~+28 since).
**Repo / branch**: `~/codemie-dev/codemie`, `EPMCDME-14786_sharing-assistants`

## Owner decisions (backend items)
- a1 (blocker): rewrite migration `a1b2c3d4e5f8` in place (not applied to any shared env): add only the uppercase `ASSISTANT` label to `resourcetype`; drop all lowercase relabels of resourcetype/principaltype/action; remove all three `values_callable` from `models/permission.py`, keeping name-based mapping. Downgrade: delete ASSISTANT grant rows, then trim the enum.
- a3: gate the four sharing endpoints on `ENABLE_USER_MANAGEMENT` (pattern: `user_management_router.py` L144, `ExtendedHTTPException(400)`); `sharable-users` requires `q` length ≥ 2 and returns at most 20 results.
- a4: no scope restriction — any registered internal user, cross-project.
- a5: reject a grant when the assistant uses a personal (USER `setting_type`) integration; reject an assistant create/update that adds a personal integration while the assistant has named grants. Error must be descriptive so the UI can show it. Mirrors the UI-only rule for project share (`AssistantSetupSection.tsx` L133-154, `AssistantForm.tsx` L602-606) but rejects instead of auto-unsetting.
- a7: durable audit of grant and revoke as activity events, same transaction as the change; mutation aborts if the event can't be stored; no-op requests write no event; when `ACTIVITY_EVENTS_ENABLED` is off, mutation proceeds with no audit; retention follows existing activity-events retention (purge job in `rest_api/main.py`), leave as is if not defined. Event: actor (durable identity in `attributes`, since `actor_id` FK is ON DELETE SET NULL), principal type + id, assistant id, project, before/after, UTC time. Covers share DELETE, generic `DELETE /v1/permissions/{id}` on ASSISTANT rows (a17), and grant removal on assistant delete (a18).
- a8: skip named-grant path for `user.is_external_user` in `is_shared_with` and both SQL predicates.
- a9: preload the page's granted assistant IDs in one query instead of per-row grant lookup via `Ability.list`.
- a13: dedupe `user_ids`; conflict-safe insert in one transaction (`unique_resource_principal` constraint L69); keep all-or-none validation.
- a14: one helper for the named-grant predicate (model L874, repo L598, repo L692); do not use `Permission.get_for`.
- a15: all four routes use `_get_assistant_by_id_or_raise` (404) instead of `get_by_id` (KeyError → 500).
- a16: share service exposes public authorized entry points; router must not call private `_require_owner`. No unrelated refactor.
- a17: generic delete keeps grantor-or-admin check for ASSISTANT rows but delegates to the share service (audited).
- a18: `delete_assistant` deletes the assistant's live grant rows; event history kept.

## Code locations
- Migration: `src/external/alembic/versions/a1b2c3d4e5f8_add_assistant_to_resourcetype.py` (revises `f88ad134467d`; currently three `op.sync_enum_values` relabels; downgrade deletes `'assistant'` rows). Migrations run at app startup (`rest_api/main.py`); old pods read labels by name.
- `src/codemie/rest_api/models/permission.py` — lowercase-valued `str, Enum`s; columns L74-94 `values_callable`; `get_for` L108 (action-blind, uses username, swallows errors). After dropping `values_callable` SQLAlchemy persists names → label must be `ASSISTANT`. `PermissionCreateRequest` payloads use values.
- `src/codemie/service/assistant/assistant_share_service.py` — `grant` L34, `revoke` L78, `list_shared_with` L98, `search_sharable_users` L125 (no limit), `_require_owner` L147.
- `src/codemie/rest_api/routers/assistant.py` — routes L3318 (GET shared-with), L3326 (POST share), L3334 (DELETE share), L3342 (GET sharable-users, `min_length=0`).
- `src/codemie/rest_api/models/assistant.py` — `is_owned_by` L848, `is_shared_with` L856, `get_by_id` L1116, `delete_assistant` L1145, `get_by_ids` L1185.
- `src/codemie/service/assistant/assistant_repository.py` — list loop L216-217; predicates L598, L692.
- `src/codemie/core/ability.py` — `can` L165 / `list` L187.
- `src/codemie/rest_api/routers/permission.py` L66 + `service/permission/permission_deletion_service.py` (no ASSISTANT branch, no audit; error message says "create permissions for").
- `src/codemie/service/activity/activity_repository.py` `insert` L138 — fail-open (returns None if disabled, catches all exceptions in `begin_nested()`); ~14 callers rely on that → a7 needs a strict variant, keep existing callers unchanged. `delete_older_than` L348 used by purge. `activity_models.py` — no assistant domain/event constants yet.
- Assistant toolkits/MCP servers carry `settings`; personal integrations have USER `setting_type`.

## Tests
Existing: `tests/codemie/service/assistant/test_assistant_share_service.py`, `tests/codemie/rest_api/routers/test_assistant_share.py`, `tests/unit/routers/test_assistant_sharing_security.py`, `tests/codemie/core/test_ability.py`, `tests/codemie/service/activity/test_activity_repository.py`.
Gaps: revoke route, `delete_permission`/`PermissionDeletionService`, migration, purge.

## Risks
- Strict audit vs fail-open repository.
- Server-side personal-integration rule has no backend precedent; must cover assistant create/update and grant.
- Enum name/value consistency after dropping `values_callable` (datasource app-permission checks share the table).
- Three predicate copies must all get the external-user skip.
