# Plan: Activity Trail Taxonomy, Categories, Entity Labels, and Metadata (EPMCDME-14325 AC15-24)

Commit per task using the repository's existing convention.

## Task 1 — Taxonomy catalogue module + enforcement event constant

Add `MEMBER_SPEND_ENFORCEMENT_UPDATED = "project.member_spend_enforcement.updated"` to
`ProjectManagementEvent` (`src/codemie/service/activity/activity_models.py:46-49`).

New `src/codemie/service/activity/activity_taxonomy.py`:
```python
EVENT_TAXONOMY: dict[str, tuple[str, str]] = {
    # event_type -> (label, category)
}
CATEGORY_EVENT_TYPES: dict[str, set[str]] = {}  # built once from EVENT_TAXONOMY
CATEGORIES = ("Budget", "Members", "Settings", "Account")


def get_label(event_type: str) -> str: ...
def get_category(event_type: str) -> str: ...
```
Populate `EVENT_TAXONOMY` with every `BudgetManagementEvent` value (category `Budget`), the three
`USER_PROJECT_*` values from `UserManagementEvent` (category `Members`), all `ProjectManagementEvent`
and `CustomerConfigEvent` values (category `Settings`), and the five account-lifecycle
`UserManagementEvent` values (category `Account`) per the spec's per-category lists.

Test-first: yes — a test in `tests/codemie/service/activity/test_activity_taxonomy.py` that reflects
over `ProjectManagementEvent`, `CustomerConfigEvent`, `UserManagementEvent`, `BudgetManagementEvent`
(filtering dunder/callable attrs) and asserts every value is a key in `EVENT_TAXONOMY` with a
non-empty label and a category in `CATEGORIES`; fails today because the module doesn't exist.

## Task 2 — `entity_label` column and validator

Add `entity_label: Optional[str] = Field(default=None, sa_column=Column(VARCHAR(255), nullable=True))`
to `ActivityEvent` (`activity_models.py:91-147`) and the same field to `ActivityEventCreate`
(`activity_models.py:150-165`). Extend the `entity_pair_must_be_complete` validator
(`activity_models.py:166-170`) to also raise when `entity_label is not None` but `entity_type is None`.

New Alembic migration under `src/external/alembic/versions/`, modeled on
`y2z3a4b5c6d7_add_project_and_actor_snapshot_to_activity_events.py`: adds nullable
`entity_label VARCHAR(255)` to `activity_events`, no backfill, `down_revision` set to the current
head revision.

Test-first: yes — a repository/model test asserting `ActivityEventCreate(entity_label="x")` without
`entity_type`/`entity_id` raises `ValueError`, and a migration test (parallel to
`test_activity_events_project_actor_migration.py`) asserting the column exists and is nullable after
upgrade.

## Task 3 — Entity label at project/membership emit sites

Populate `entity_label` at the emit sites in `project_service.py` (project created/updated/deleted —
project display name or name), `project_assignment_service.py` (four sites, lines ~419, ~500, ~626,
~739 — assignee's display name or email), and `personal_project_service.py`'s emit site (project
name). Each site passes a resolved string already available in scope (`project.display_name or
project.name`, `user.name or user.email`) — no new lookups.

Test-first: yes — extend each file's existing activity-emission tests to assert the constructed
`ActivityEventCreate` includes the expected non-null `entity_label`.

## Task 4 — Entity label at budget emit sites

Populate `entity_label` at the emit sites in `budget_service.py`, `project_budget_service.py`,
`active_project_budget_restore_service.py`, `inactive_project_budget_stop_service.py`, and
`inactive_cost_center_budget_stop_service.py` — budget name, project-budget-group name, or budget
assignment's budget name, per site, from values already resolved in each method.

Test-first: yes — extend `tests/codemie/service/budget/test_budget_service_activity.py` and the
sibling budget-service test files to assert non-null `entity_label` on each constructed event.

## Task 5 — Entity label at user/auth/config emit sites

Populate `entity_label` at the emit sites in `user_management_service.py`, `registration_service.py`,
`authentication_service.py`, `local_auth_router.py` (user display name or email), and
`customer_config_service.py` (setting key/name).

Test-first: yes — extend the corresponding existing tests for each of these five files to assert
non-null `entity_label`.

## Task 6 — Conditional member-spend-enforcement event

In `ProjectService.update_project` (`project_service.py:189-262`), before the
`enforce_member_spend_limits is not None` branch at line 240, read
`current_enforced = SettingsService.get_enforce_member_spend_limits(project.name)`. When
`enforce_member_spend_limits is not None and enforce_member_spend_limits != current_enforced`, emit
a second `ActivityEventCreate` with `event_type=ProjectManagementEvent.MEMBER_SPEND_ENFORCEMENT_UPDATED`,
same `entity_type`/`entity_id`/`actor_id`/`project_name`/actor-snapshot fields as the existing
`PROJECT_UPDATED` emission, `attributes={"enforce_member_spend_limits": enforce_member_spend_limits}`.
The existing unconditional `PROJECT_UPDATED` emission at line 245 is unchanged.

Test-first: yes — a test calling `update_project` with `enforce_member_spend_limits=True` when the
current setting is `False` asserts two `activity_event_repository.insert` calls (one
`PROJECT_UPDATED`, one `MEMBER_SPEND_ENFORCEMENT_UPDATED`); a second test with the new value equal to
the current value asserts only the `PROJECT_UPDATED` call.

## Task 7 — Category filtering in repository, service, router

`find_by_project` (`activity_repository.py:314-379`) gains `category: list[str] | None = None`.
When provided, resolve `event_type`s via `activity_taxonomy` (union of `CATEGORY_EVENT_TYPES[c]` for
each requested category) and add `ActivityEvent.event_type.in_(resolved_types)` to `conditions`,
applied to both `count_stmt` and `data_stmt` — do not filter on `domain`. Thread `category` through
`ActivityEventService.list_project_events` (`activity_event_service.py:71-112`) and add
`category: list[str] | None = Query(None)` to the router
(`project_activity_events_router.py:38-61`), forwarded the same way as `domain`/`event_type`.

Test-first: yes — a repository test selecting category `Budget` asserts the resulting `event_type IN
(...)` includes both a `BudgetManagementEvent` value and `MEMBER_SPEND_ENFORCEMENT_UPDATED`, and that
`total` reflects only the filtered rows.

## Task 8 — Response rows, recording_enabled, project response envelope

`ActivityEventListItem` (`rest_api/models/activity_event.py:23-34`) gains `label: str`, `category:
str` (via `activity_taxonomy.get_label`/`get_category`), and `entity_label: str | None`. New
`ProjectActivityEventsResponse(BaseModel)` in the same file with `data: list[ActivityEventListItem]`,
`pagination: PaginationData`, `recording_enabled: bool` — a project-scoped envelope distinct from the
shared `PaginatedListResponse` used by the global endpoint (left untouched). `list_project_events`
reads `config.ACTIVITY_EVENTS_ENABLED` first: when `False`, returns `([], 0, recording_enabled=False)`
without calling the repository; when `True`, calls `find_by_project` as today and returns
`recording_enabled=True`. The router's response model and return type change to
`ProjectActivityEventsResponse`.

Test-first: yes — a service test with `config.ACTIVITY_EVENTS_ENABLED = False` asserts
`list_project_events` returns `recording_enabled=False`, empty items, `total=0`, and does not call
`activity_event_repository.find_by_project`; a router test asserts the response body's
`recording_enabled` field matches.

## Task 9 — Metadata endpoint

New `SQLActivityEventRepository.get_distinct_event_types_by_project(project_name, session)` — parallel
to `get_domain_mapping` (`activity_repository.py:398-422`), `SELECT DISTINCT event_type FROM
activity_events WHERE project_name = :project_name`. New
`ActivityEventProjectMetadata(BaseModel)` (`event_types: list[dict]` — each `{event_type, label,
category}` — plus `recording_enabled: bool`) in `rest_api/models/activity_event.py`. New
`ActivityEventService.get_project_event_metadata(project_name)` applying the same
`ACTIVITY_EVENTS_ENABLED` short-circuit as task 8, else mapping each distinct event type through
`activity_taxonomy`. New `GET /v1/projects/{project_name}/activity-events/metadata` on
`project_activity_events_router.py`, same `project_admin_or_admin_or_maintainer_access` dependency,
same `ExtendedHTTPException` passthrough pattern as the existing route.

Test-first: yes — a router test asserts a non-project-admin caller (dependency raises 403) gets no
data; a service test with two distinct event types in a project's rows asserts both appear with
correct label/category and no others; a disabled-flag test asserts `recording_enabled=False` with an
empty `event_types` list.

## Negative constraints

- Category is never derived from `domain` (task 7 resolves via the taxonomy's reverse index only,
  never a `domain IN (...)` condition) — satisfies "category is derived... and distinct from domain."
- No backfill of `entity_label` on pre-existing rows (task 2's migration adds the column with no
  `UPDATE`/backfill statement) — satisfies "no backfill."
- The existing unconditional `PROJECT_UPDATED` emission is not removed or made conditional (task 6
  only adds a second, conditional emission) — satisfies "additive... existing emission unchanged."
- No categories beyond `Budget`/`Members`/`Settings`/`Account` are introduced (task 1's `CATEGORIES`
  tuple is closed) — satisfies "no categories beyond the four fixed values."
- The global (non-project-scoped) activity events router/response (`activity_events_router.py`,
  `ActivityEventFilterOptions`) is not modified by any task — satisfies "not reworking the existing
  global endpoint."
- No frontend/UI changes are planned in any task — satisfies "no UI rendering" non-goal.
- Task 9's metadata endpoint uses the identical auth dependency as the trail endpoint, never a looser
  one — satisfies "authorized identically to the trail" and "denied caller gets no data leaked."
