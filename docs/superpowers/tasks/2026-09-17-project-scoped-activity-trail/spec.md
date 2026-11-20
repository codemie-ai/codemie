# Spec: Project-Scoped Activity Trail (EPMCDME-14325)

## Summary

Add project attribution and actor-snapshot columns to `activity_events`, populate them at all
~36 emit sites, and expose a new project-scoped read endpoint authorized for that project's
admins or platform admins/maintainers. The existing global endpoint is untouched.

## Data model changes

`src/codemie/service/activity/activity_models.py` — add to `ActivityEvent`:
- `project_name: str | None` — denormalized, nullable, no FK. Null means "no project context"
  (never empty string). Index on `(project_name, created_at)`.
- `actor_email: str | None`, `actor_display_name: str | None` — snapshot at write time.

Mirror both additions on `ActivityEventCreate` (`activity_models.py`) as optional fields with the
same null-means-absent semantics, validated the same way `entity_type`/`entity_id` pairing is
validated today.

## Migration

New Alembic revision under `src/external/alembic/versions/`. Per
`.ai-run/guides/data/database-patterns.md`, inspect current heads first (analysis found 29) and
set `down_revision` to the actual current head touching `activity_events`
(`38255069bfab_create_activity_events_table.py`), not an assumed one — resolve at plan time via
`alembic heads`. `upgrade()` only adds the three nullable columns and the new composite index;
it must not touch `domain`, `event_type`, `entity_id`, `actor_id`, or `created_at` on existing
rows (AC13). Add a migration-safety test asserting pre-existing row values are unchanged after
upgrade — this closes the gap the analysis flagged (no such test exists today).

## Write path: 36 emit sites, 10 files

Each `ActivityEventCreate(...)` call site (listed in `technical-analysis.md` §2) gets two new
arguments:
- `project_name`: the project name already available at that call site, or `None` where there is
  no project context. Concretely: `project_service.py`'s three sites pass the project's own name;
  `personal_project_service.py`'s site passes `None` (personal projects are never project-scoped,
  so they must not populate this field — this is how AC9's exclusion is enforced, not a runtime
  filter); `project_assignment_service.py`'s five sites read the existing
  `attributes["project_name"]` value they already compute and pass it as the new field too
  (attributes stays unchanged, for backward compatibility with the global endpoint's response
  shape); the two already-correct budget sites (`project_budget_service.py:994,1940,2114`) do the
  same; the remaining budget sites pass `project_name` from the owning
  `ProjectBudgetAssignment`/`ProjectBudgetGroup`/`ProjectMemberBudgetAssignment` model
  (`budget_models.py:104,212,259,306`), which already carries it; user-management/auth/scheduler
  sites with no project context pass `None`.
- `actor_email`, `actor_display_name`: read from the already-in-scope `user`/`actor` object at
  each site (no new query). The two scheduler sites with `actor_id=None`
  (`active_project_budget_restore_service.py`, `inactive_project_budget_stop_service.py`) pass
  `None` for both — actor-snapshot must tolerate a missing actor, matching existing `actor_id`
  handling there.

No shared factory exists today (analysis confirms this), so each site is edited individually and
verified against its own existing test (per `test_budget_service_activity.py`'s pattern) or a new
one where AC coverage is missing.

## Read path

`SQLActivityEventRepository` (`activity_repository.py`) gains a `find_by_project` method
following the existing `find_all` shape: same `conditions`-list-reused-for-count pattern (AC11),
filters on `project_name = :name`, plus the existing `domain`/`event_type`/`actor_id`/time-range
filters, ordered `created_at DESC` with a secondary `id DESC` tie-break for deterministic
pagination (AC12). It selects the snapshot `actor_email`/`actor_display_name` columns directly;
the existing `UserDB` outerjoin becomes a `COALESCE` fallback only when the snapshot is null
(pre-migration rows), so `find_all`/the global endpoint keep their exact current behavior (AC10)
since they don't call this new method. Gate with `config.ACTIVITY_EVENTS_ENABLED`: when false,
return an empty result/total (AC14) — do this check in the repository method itself, consistent
with the existing write-path convention of gating in the repository, not the router.

`ActivityEventService` gains `list_project_events(project_name, filters, pagination)`, a thin
orchestration method mirroring `list_events`.

## API

New router, new prefix (e.g. `/v1/projects/{project_name}/activity-events`, following the
existing `APIRouter(prefix=..., dependencies=[Depends(authenticate)])` convention seen in
`mcp_managed.py`), with a new `Depends`-based authorization function in
`authentication.py` modeled directly on `project_admin_or_admin_user_detail_access`
(`authentication.py:255-320`): admins/maintainers pass; otherwise read `project_name` from
`request.path_params`, call `user_project_repository.is_admin(session, user.id, project_name)`,
and 403 with the same `ExtendedHTTPException`/`ACCESS_DENIED_MESSAGE` triad on denial (AC7).
Denial returns no events, counts, or existence information — a 403 with no body leakage.
Response reuses `ActivityEventListItem`/`PaginationData` shapes from the global endpoint's models
file; filters accepted: `domain`, `event_type`, `actor_id`, `from`/`to`, `sort_dir`, `limit`,
`offset` (no `entity_type`/`entity_id`, which don't apply to a project-scoped view).

## Non-goals

- No UI (consumed by EPMCDME-14326).
- No retention policy or enabled-by-default change (Story 6).
- No new event types or newly recorded values beyond project/actor snapshot (Stories 3, 4).
- No changes to the existing global `/v1/admin/activity-events` endpoint's behavior, filters, or
  authorization.
- No backfill of pre-existing rows; they remain project-less and excluded from project-scoped
  results permanently.
- No change to `attributes` JSON shape at any emit site (kept for backward compatibility).
- No derivation of project attribution via joins at read time — always the write-time snapshot.

## Acceptance criteria

See `technical-analysis.md` §1 Acceptance Criteria (AC1–AC14) — this spec's design satisfies each
one as cited inline above.
