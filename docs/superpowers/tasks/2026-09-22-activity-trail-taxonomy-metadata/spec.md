# Spec: Activity Trail Taxonomy, Categories, Entity Labels, and Metadata (EPMCDME-14325 AC15-24)

## Context

Builds on the merged-to-branch project-scoped activity trail (`ActivityEvent` model,
`activity_repository.py`, `activity_event_service.py::list_project_events`,
`project_activity_events_router.py`, `project_admin_or_admin_or_maintainer_access`), all on
`EPMCDME-14325_project-scoped-activity-trail`. This spec covers backend-only additions: a
taxonomy catalogue, category filtering, entity-label snapshotting, a dedicated member-spend-
enforcement event, a metadata endpoint, and explicit recording-state reporting.

## Taxonomy Catalogue

New module (e.g. `src/codemie/service/activity/activity_taxonomy.py`) holding a single source of
truth: a dict keyed by every `event_type` string defined across `ProjectManagementEvent`,
`CustomerConfigEvent`, `UserManagementEvent`, `BudgetManagementEvent`
(`src/codemie/service/activity/activity_models.py:46-88`) plus the new enforcement event type,
mapping each to `(label: str, category: str)`. Categories: `Budget`, `Members`, `Settings`,
`Account` (four fixed values, not derived from `domain`).

- **Budget** — every `BudgetManagementEvent` type, plus the new member-spend-enforcement event.
- **Members** — `USER_PROJECT_ASSIGNED`, `USER_PROJECT_REMOVED`, `USER_PROJECT_ROLE_UPDATED`
  (project-membership changes only; emitted from `project_assignment_service.py`, domain
  `USER_MANAGEMENT`).
- **Settings** — `ProjectManagementEvent` (created/updated/deleted) and `CustomerConfigEvent`
  (setting updated/reset).
- **Account** — `USER_CREATED`, `USER_UPDATED`, `USER_DEACTIVATED`, `USER_LOGIN`, `USER_LOGOUT`
  (account lifecycle, no project membership involved).

A reverse index (category → set of event types) is built from this dict once, for use by the
category filter.

## Member-Spend-Enforcement Event

Add `MEMBER_SPEND_ENFORCEMENT_UPDATED` to `ProjectManagementEvent` (domain stays
`PROJECT_MANAGEMENT`; category `Budget` via the catalogue). In `project_service.py::update_project`
(around `src/codemie/service/project/project_service.py:550`), before calling
`SettingsService.set_enforce_member_spend_limits`, read the current value via
`SettingsService.get_enforce_member_spend_limits(project.name)`. Emit the new event only when the
resolved new value differs from the current one. This is additive: the existing unconditional
`PROJECT_UPDATED` emission on `update_project` is unchanged; the enforcement event fires alongside
it when the flag actually changes, and not at all when it doesn't.

## Entity Label Snapshot

Add a nullable `entity_label: str | None` column (VARCHAR 255) to `ActivityEvent`, with an Alembic
migration (no backfill of existing rows — they remain `NULL`). Extend `ActivityEventCreate`'s
validator so `entity_label` must be `None` when `entity_type`/`entity_id` are `None`, mirroring the
existing pair rule (`activity_models.py:166-170`). Each of the ~36 emit sites passes a
human-readable name for the entity involved (project display name, budget name, user display
name/email, budget-group name) at write time; once written, the label never changes even if the
underlying entity is later renamed or deleted.

## Category Filtering

`find_by_project` gains a `category: list[str] | None` parameter. Because category is not a
column, it is translated into an `event_type IN (...)` condition using the taxonomy's reverse
index, applied identically to both the count and data statements (following the existing
conditions-list pattern) so total reflects the filtered set. The router
(`project_activity_events_router.py`) adds `category: list[str] | None = Query(None)`, forwarded
through `list_project_events`, list-valued like the existing `domain`/`event_type` filters.

## Response Rows and Recording State

`ActivityEventListItem` gains `label: str` and `category: str` (resolved from the catalogue by
event type) alongside existing `domain`/`event_type`, plus `entity_label: str | None`.

Both the trail response and the new metadata response gain a top-level `recording_enabled: bool`
field. The service layer reads `config.ACTIVITY_EVENTS_ENABLED` directly and short-circuits before
calling the repository when it is `False`, returning `recording_enabled=False` with an empty
list/zero total. When enabled, `recording_enabled=True` regardless of row count — this is what
makes "disabled" distinguishable from "enabled, zero events," which the current `find_by_project`
collapses.

## Metadata Endpoint

New `GET /v1/projects/{project_name}/activity-events/metadata` on the same router, using the same
`project_admin_or_admin_or_maintainer_access` dependency. A new repository method (parallel to
`get_domain_mapping`) selects `DISTINCT event_type` from `activity_events` where
`project_name = :project_name`; the service maps each to `{event_type, label, category}` via the
catalogue and returns them alongside `recording_enabled`. No pagination — the result set is at
most the number of distinct event types in the catalogue.

## Acceptance Criteria (AC15-24)

- Every returned event row carries `label` and `category` alongside `domain`/`event_type`.
- A `category` filter narrows the trail server-side, with `total` reflecting the filtered set.
- Selecting `Budget` returns budget events and the enforcement event together.
- The enforcement event fires only when the flag actually changes on a project update.
- Events with an entity get a label snapshotted at write time; events without one get `null`.
- A later rename/delete of the entity does not change the stored label.
- The metadata endpoint lists only event types present in that project's trail, each with label
  and category, under identical authorization to the trail.
- A non-project-admin caller gets denied on the metadata endpoint with no data leaked.
- Disabled recording is reported explicitly and distinguishably from enabled-with-zero-events, on
  both endpoints.
- A test reflects over all four event-type enum classes (plus the new enforcement type) and fails
  if any lacks a catalogue entry with both label and category.

## Non-Goals

- Capturing previous/new field values on update events (EPMCDME-14328).
- Any frontend/UI rendering of categories, labels, or the metadata endpoint (EPMCDME-14326).
- Reworking the existing global (non-project-scoped) activity events endpoint or its filter-options
  response.
- Retention policy changes.
- Backfilling `entity_label` on rows written before this change.
- Introducing categories beyond the four fixed values (Budget, Members, Settings, Account).

## Open Risks

- Label resolution must be added at ~36 emit sites with no existing shared convention — mechanical
  but wide, and easy to miss a site during review.
- The reflective completeness test depends on event-type constants staying plain string class
  attributes; a future non-string constant or renamed class would silently escape it.
- Pre-migration rows keep `entity_label = NULL` permanently by design; any consumer must treat that
  as normal, not as a bug.
