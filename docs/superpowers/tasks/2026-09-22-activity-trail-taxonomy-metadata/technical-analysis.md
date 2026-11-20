# Technical Research

**Task**: activity events taxonomy category
**Generated**: 2026-09-22T00:00:00Z
**Research path**: filesystem

---

## 1. Original Context

EPMCDME-14325 ticket update — extend the already-implemented project-scoped activity trail with: a backend event taxonomy catalogue mapping every event type to a display label and a category (Budget, Members, or Settings; category is derived, not stored, and distinct from domain); a dedicated event type emitted specifically when a project's member-spend-enforcement flag changes (and no enforcement event when the flag is unchanged); a readable target/entity label snapshotted on the event row at write time (null when the event concerns no entity); a new project-scoped event-metadata endpoint that lists only the event types present in that project's trail (each with label and category), authorized identically to the trail endpoint; explicit reporting of recording state when ACTIVITY_EVENTS_ENABLED is false, distinguishable from an enabled project with zero events, for both the trail and the metadata endpoint; category filtering on the project trail with server-side total count over the filtered set (Budget category must include budget events plus the member-spend-enforcement event despite differing domains); and a test asserting every emittable event type has both a label and a category, failing if a new event type lacks either. This builds on the completed EPMCDME-14325 work already on branch EPMCDME-14325_project-scoped-activity-trail: ActivityEvent model with project_name/actor-snapshot columns, migration, per-domain emit sites (project domain events, project membership events, project budget events, non-project budget events, user management/auth events), list_project_events service method, find_by_project repository method with ACTIVITY_EVENTS_ENABLED gating, project-admin-or-maintainer auth dependency, and the project-scoped activity events router. Research: where ActivityEvent/activity_events model, repository, and emit call sites live; where event_type values are defined/enumerated across domains (project_management, user_management, budget_management); where the member-spend-enforcement flag lives on the project model and where it's updated; how entity/target identifiers are currently captured on events (if at all); the existing project-scoped router and service (list_project_events, find_by_project) and their pagination/filtering/auth patterns to extend for metadata + category filtering; existing test patterns for activity events.

---

## 2. Codebase Findings

### Existing Implementations

- `src/codemie/service/activity/activity_models.py` — `ActivityEvent` SQLModel table (`activity_events`), `ActivityEventCreate` pydantic input model, and the event-type enumeration classes: `ActivityDomain` (`user_management`, `budget_management`, `project_management`, `customer_config`), `ActivityEntityType`, `ProjectManagementEvent` (`project.created`, `project.updated`, `project.deleted` — no dedicated enforcement-toggle event), `CustomerConfigEvent`, `UserManagementEvent` (8 event types), `BudgetManagementEvent` (20 event types). These four classes are the complete, closed enumeration of every emittable `event_type` string in the codebase today — no other file defines event-type constants.
- `ActivityEvent` columns: `id`, `domain`, `event_type`, `entity_type`, `entity_id`, `actor_id`, `attributes` (JSONB), `project_name`, `actor_email`, `actor_display_name`, `created_at`. There is **no column that stores a human-readable target/entity label** — `entity_id` is consistently a raw identifier (`budget.budget_id`, `user_id`, project name, assignment id), never a display name. Where a readable value exists it sometimes appears inside the free-form `attributes` JSONB dict (e.g. `attributes={"budget_id": ...}`, `attributes={"category": category.value}`, `attributes={"project_name": ..., "is_project_admin": ...}`), but this is inconsistent per call site and not queryable/projected as a distinct field.
- `src/codemie/service/activity/activity_repository.py` — `ActivityEventRepository` (ABC) and `SQLActivityEventRepository`. Key methods: `insert`/`async_insert` (both gated by `config.ACTIVITY_EVENTS_ENABLED`, silently no-op and return `None` when disabled), `find_all`, `find_by_project` (also gated by `ACTIVITY_EVENTS_ENABLED`, returning `([], 0)` when disabled — this is the same shape as "enabled with zero events", which the task calls out as needing to become distinguishable), `get_distinct_domains`, `get_distinct_event_types`, `get_distinct_entity_types`, `get_domain_mapping` (groups distinct `event_type`/`entity_type` per domain, used for the existing global filter-options endpoint), `delete_older_than`.
- `src/codemie/service/activity/activity_event_service.py` — `ActivityEventService` with `list_events` (global), `list_project_events` (project-scoped, delegates to `find_by_project`), `get_filter_options` (global, domain-based mapping only — no project-scoped equivalent and no category concept anywhere).
- `src/codemie/rest_api/models/activity_event.py` — `ActivityEventListItem` (response row shape — no label/category fields), `DomainFilterEntry`, `ActivityEventFilterOptions` (global-only, keyed by domain).
- `src/codemie/rest_api/routers/project_activity_events_router.py` — `GET /v1/projects/{project_name}/activity-events`, the only project-scoped activity endpoint today. Filters: `domain`, `event_type`, `actor_id`, `from`/`to`, `sort_dir`, `limit`/`offset` (no category filter). Auth: `Depends(project_admin_or_admin_or_maintainer_access)`. Builds `PaginatedListResponse[ActivityEventListItem]` with `PaginationData(page, per_page, total, pages)` computed from `offset//limit` and `math.ceil(total/limit)`.
- 36 emit call sites across 10 files (per the prior task's own analysis, still current): `project_service.py` (project create/update — `PROJECT_UPDATED` fires unconditionally on every `update_project` call regardless of which fields actually changed, including when only unrelated fields like `display_name` change and `enforce_member_spend_limits` is untouched), `project_assignment_service.py` (membership events), `budget_service.py`, `project_budget_service.py`, `active_project_budget_restore_service.py`, `inactive_project_budget_stop_service.py`, `inactive_cost_center_budget_stop_service.py` (budget events, mostly `project_name=None`), `authentication_service.py`, `registration_service.py`, `user_management_service.py`, `local_auth_router.py` (user-management/auth events), `customer_config_service.py` (customer_config events), `personal_project_service.py`.

### Architecture and Layers Affected

- **Model layer**: `activity_models.py` — event-type enumeration classes are the source of truth a taxonomy catalogue must map over; `ActivityEvent` table for any new label column.
- **Repository layer**: `activity_repository.py` — `find_by_project` for category filtering and the disabled-vs-empty distinction; a new method would be needed for "distinct event types present in this project's trail" (metadata endpoint), analogous to `get_distinct_event_types`/`get_domain_mapping` but project-scoped.
- **Service layer**: `activity_event_service.py` — `list_project_events` (extend for category filter) and a new metadata method analogous to `get_filter_options`.
- **API layer**: `project_activity_events_router.py` — existing trail endpoint (extend for category filter); a new sibling endpoint for event metadata, same router or new one, same auth dependency.
- **Emit sites**: `project_service.py::update_project` is the exact site where `enforce_member_spend_limits` is read as an optional parameter (`None` = unchanged, `bool` = explicit set) and passed to `SettingsService.set_enforce_member_spend_limits`; this is the natural point to detect an actual change and conditionally emit a distinct event type instead of (or alongside) `PROJECT_UPDATED`.
- **Settings layer**: `src/codemie/service/settings/settings.py` — `SettingsService.get_enforce_member_spend_limits(project_name)` / `set_enforce_member_spend_limits(project_name, enabled)`. The flag itself is not a column on the project/`Application` model; it is persisted as a project-scoped setting (alias `ENFORCE_MEMBER_SPEND_LIMITS_ALIAS`, credential type `ENVIRONMENT_VARS`, `SettingType.PROJECT`), read back via `retrieve_setting(...).normalize_values().get(cls.ENABLED)`.

### Integration Points

- `src/codemie/rest_api/routers/projects.py` exposes `enforce_member_spend_limits` on project create/update/response DTOs (lines ~228, 252, 265, 746, 897, 1097, 1111) and calls into `ProjectService.update_project`, which is where the flag-change detection must happen — `update_project` already receives the *new* value as a parameter but does not compare it against the *current* persisted value before calling `SettingsService.set_enforce_member_spend_limits`; the current value is only obtainable via `SettingsService.get_enforce_member_spend_limits(project.name)` before the write.
- `src/codemie/enterprise/litellm/project_member_runtime_sync.py::resync_project_member_allocations_sync` is invoked after a flag change (`_resync_member_allocations_if_needed`) — confirms the flag change is already a distinguishable business event, just not yet an activity event.
- `config.ACTIVITY_EVENTS_ENABLED` (bool, default `False`, `src/codemie/configs/config.py:350`) gates both repository write paths and `find_by_project` read path, and gates the retention scheduler in `main.py`. Any "recording disabled" signal in API responses must originate from this same flag being read at the router/service layer rather than solely inferred from an empty result set.

### Patterns and Conventions

- Event-type constants are plain string class attributes on domain-named classes (`ProjectManagementEvent`, `BudgetManagementEvent`, etc.), not an enum — a taxonomy catalogue would iterate `vars()`/`__dict__` of these classes or maintain an explicit dict keyed by the same string literals.
- All list/filter methods follow: repository builds a `conditions` list, applies to both a `count_stmt` (for total) and a `data_stmt` (for rows), executed in the same session — this is the established server-side-count pattern the category filter and metadata "distinct event types in project" query should follow.
- Response models are flat pydantic `BaseModel`s with no shared base class; `PaginatedListResponse[T]`/`PaginationData` (`src/codemie/rest_api/models/base.py`, referenced but not read in this pass) is the generic pagination envelope reused by the project trail router.
- Auth: `project_admin_or_admin_or_maintainer_access` (`src/codemie/rest_api/security/authentication.py:343`) is a `Depends`-based FastAPI dependency reading `request.state.user` and `request.path_params["project_name"]`; the task requires the new metadata endpoint use this identical dependency.

---

## 3. Documentation Findings

### Guides and Architecture Docs

No guides found under `.ai-run/guides/` covering activity events specifically — conventions derived from code exploration and from the prior task's own artifacts (see below).

### Architectural Decisions

- `docs/superpowers/specs/2026-07-15-activity-events-design.md` and `docs/superpowers/specs/2026-07-15-activity-events-read-api-design.md` — original design specs for the append-only `activity_events` table and its first (global, maintainer-only) read API; not re-read in full this pass, but establish the entity_type+entity_id generic-reference pattern and the disabled-by-default posture.
- `docs/superpowers/tasks/2026-09-17-project-scoped-activity-trail/` — the immediately preceding completed task for this same ticket. Its `technical-analysis.md` documents: project attribution added as a denormalized non-FK `project_name` column populated explicitly at ~36 emit sites (chosen over query-time joins or an `attributes.project_name` convention); actor email/display name snapshotted at write time (chosen over relying solely on the read-time join to `users`, which breaks on user deletion). Its `spec.md`/`plan.md`/13 `task-N-report.md` files document the concrete construction of `find_by_project`, the router, and the auth dependency — useful as precedent for how server-side count-over-filtered-set and the disabled/enabled semantics were previously reasoned about, though that prior work left `find_by_project` returning `([], 0)` for the disabled case, which is exactly what this task must now make distinguishable from "enabled, zero rows."
- `docs/activity-events-ui-plan.md` — frontend plan for the existing *global* (maintainer-only) activity events page; confirms the current UI/API contract has no category or label concept and consumes `entity_type`/`entity_id` as raw strings in its "Entity" column, with "system" as the actor fallback — useful as the shape a future project-scoped UI would likely mirror, but out of scope for this backend-only task.

### Derived Conventions

- Every new emit site continues the `ActivityEventCreate(...)` construction pattern with explicit `domain=`, `event_type=`, `entity_type=`/`entity_id=` pair-or-both-None, `actor_id=`, `project_name=`, `actor_email=`, `actor_display_name=` keyword arguments passed to `activity_event_repository.insert(event, session)` inside the same DB transaction as the business mutation.
- Distinct-value repository methods (`get_distinct_domains`, `get_distinct_event_types`, `get_distinct_entity_types`, `get_domain_mapping`) all query `select(...).distinct().order_by(...)` directly against `ActivityEvent`, no caching layer — the project-scoped metadata endpoint's "distinct event types present in this project" query would follow the same shape but add `.where(ActivityEvent.project_name == project_name)`.

---

## 4. Testing Landscape

### Existing Coverage

- `tests/codemie/service/activity/test_activity_models.py` — model-level tests (not read in full this pass; likely covers `ActivityEventCreate` validation).
- `tests/codemie/service/activity/test_activity_repository.py` — repository tests, uses `@patch(...config...)` with `mock_config.ACTIVITY_EVENTS_ENABLED = True/False` (line ~324 has an explicit `False` case) to exercise the enable/disable gating on `insert`/`find_by_project`.
- `tests/codemie/service/activity/test_activity_event_service.py` — `TestListEvents`, `TestListProjectEvents`, `TestGetFilterOptions` classes, each patching `activity_event_repository`, `Session`, `PostgresClient` and asserting filters/kwargs are forwarded and rows are mapped to `ActivityEventListItem`. This is the exact pattern a new `get_project_event_metadata`-style service method's tests would follow.
- `tests/codemie/rest_api/routers/test_project_activity_events_router.py` — router-level tests calling `list_project_activity_events(...)` directly (not via TestClient) with `_=None` for the auth dependency, patching `activity_event_service`, and asserting pagination math and exception propagation (`ExtendedHTTPException` passthrough vs. generic exception wrapped as 500).
- `tests/codemie/rest_api/routers/test_activity_events_router.py` — tests for the older global router.
- `tests/8F/5D/.../external/alembic/test_activity_events_project_actor_migration.py` — migration test for the `project_name`/actor-snapshot columns added in the prior task.
- `tests/codemie/service/budget/test_budget_service_activity.py` — asserts budget-service emit sites construct the expected `ActivityEventCreate` calls.

### Testing Framework and Patterns

- pytest, with `unittest.mock.patch`/`MagicMock` used pervasively to mock `Session`, `PostgresClient`, and the repository singleton rather than hitting a real DB in service/router tests; repository tests patch `config` directly to flip `ACTIVITY_EVENTS_ENABLED`.
- No fixtures/factories specific to activity events beyond local `_row()`/`_item()` builder helper functions defined per test file.

### Coverage Gaps

- No existing test enumerates or validates the event-type constant classes (`ProjectManagementEvent`, `UserManagementEvent`, `BudgetManagementEvent`, `CustomerConfigEvent`) against any label/category mapping — this is the exact gap the task's required "every emittable event type has a label and category" test would fill; there is currently no taxonomy module for such a test to import.
- No test exercises `update_project` for whether `enforce_member_spend_limits` changed vs. was merely passed through unchanged — `test_budget_service_activity.py` and any `project_service` activity tests were not opened in this pass but the described behavior (dedicated event only on actual change) has no current analog to compare against.
- No test asserts the disabled-vs-empty distinction at the API/service response level (only at the repository return-shape level, which is currently identical for both cases: `([], 0)`).
- No test covers category filtering, since no category concept exists in code yet.

---

## 5. Configuration and Environment

### Environment Variables

- `ACTIVITY_EVENTS_ENABLED` (bool, default `False`, `src/codemie/configs/config.py:350`) — master toggle read by `activity_repository.insert`/`async_insert`/`find_by_project` and by `main.py`'s retention-scheduler setup. This is the flag the task requires be explicitly surfaced (not just implicitly inferred from empty results) in both the trail and metadata endpoint responses.
- `ACTIVITY_EVENTS_RETENTION_DAYS` — referenced in `main.py:586`, gates the daily purge scheduler; not directly relevant to this task but shares the enable/disable posture.

### Configuration Files

- `src/codemie/configs/config.py` — central config class defining `ACTIVITY_EVENTS_ENABLED` and related settings; no `.env.example` entry was found for it in this pass's grep (only the `config.py` default and 4 code-site usages appeared).

### Feature Flags and Deployment Concerns

- `ACTIVITY_EVENTS_ENABLED` functions as the only feature flag in this domain — ships disabled by default per the prior task's design doc. No Docker/CI/CD references to this flag were found in this pass.

---

## 6. Risk Indicators

- Speculative: Distinguishing "recording disabled" from "enabled, zero events" for both endpoints likely requires a response-envelope change (e.g., an extra field/flag) rather than purely a repository-layer change, since `find_by_project` already collapses both cases to `([], 0)` — any fix at only the repository layer would still need the router/service to read `config.ACTIVITY_EVENTS_ENABLED` independently and surface it, doubling the places this flag is checked.
- Speculative: The "Budget category must include the member-spend-enforcement event despite differing domains" requirement means category cannot be a pure derivation of `domain` (which is `project_management` for project events); the taxonomy catalogue's category-per-event-type mapping is therefore the only source of truth for category, and the category filter on `find_by_project` cannot be implemented as a simple `domain IN (...)` SQL condition — it needs an `event_type IN (...)` expansion driven by the catalogue, or a computed-in-Python filter, which changes the shape of the existing `conditions` list pattern in `find_by_project`.
- No existing readable-label field or convention on `ActivityEvent`/`ActivityEventCreate` — every current attribute site stores raw IDs in `entity_id`, so snapshotting a readable label is new schema surface (new column) and new logic at up to 36 emit sites to resolve a display name at write time (e.g., project display name, budget name, user name) — a change of this shape is exactly the kind of ripple the task description flags as building on "the completed work" without itself specifying the schema, so the exact column/shape is a design decision, not something discovered in code.
- `update_project` currently emits `PROJECT_UPDATED` unconditionally on every call, including no-op calls where nothing changed; introducing "no enforcement event when the flag is unchanged" logic requires reading the pre-update value via `SettingsService.get_enforce_member_spend_limits` before calling `SettingsService.set_enforce_member_spend_limits`, since `update_project`'s current code path has no other record of the prior value at that point.
- Event-type constants are plain string class attributes (not an enum, no registry/decorator), so any "every emittable event type has label+category" test must reflect over these four classes via `vars()`/`dir()` filtering dunder/callable attributes — a fragile pattern if a future event type constant is added with a non-string value or a class is renamed; no existing helper for this enumeration exists in the codebase to reuse.
- The 36 emit sites span 10 files across three service domains with no shared base/decorator abstraction — any change to `ActivityEventCreate`'s required fields (e.g. adding a mandatory label parameter) is a wide, mechanical but non-trivial ripple across all of them, several of which already omit `project_name`/`actor_email`/`actor_display_name` (passing `None`) inconsistently.

---

## 7. Summary for Complexity Assessment

This task extends an already-shipped project-scoped activity trail (model, repository, service, router, migration all exist and are tested) rather than building it from scratch. It touches the model layer (`activity_models.py` — a new label/category-adjacent column and possibly a new `ProjectManagementEvent` constant), the repository layer (`activity_repository.py::find_by_project` — category filtering, disabled-vs-empty distinction, a new "distinct event types in this project" query), the service layer (`activity_event_service.py` — new metadata method, category param threading), the API layer (a new router endpoint plus extending the existing one), and one specific emit site (`project_service.py::update_project` — conditional dedicated event on flag change, requiring a pre-read of the current flag value via `SettingsService`). A new taxonomy catalogue module mapping every event-type constant across four enumeration classes to (label, category) is itself new code with no existing analog, plus the test that must enumerate those constants reflectively.

Technical novelty is moderate: the individual patterns (server-side count-over-filtered-set, `Depends`-based project auth, config-gated repository writes) are all well established elsewhere in this codebase and the immediately preceding task's artifacts are directly reusable as precedent. The main novel piece is the category concept itself, which must cut across domains (Budget category spans `budget_management` domain plus one `project_management`-domain event), meaning category cannot be derived from the existing `domain` column and needs its own mapping-driven filter logic rather than a simple SQL `IN` on an existing indexed column.

Test coverage posture is strong for the existing trail (repository, service, router all covered with established mock patterns) but there is zero existing coverage for anything category- or label-related, since neither concept exists in code yet — every test the task requires (label/category completeness, category filtering, disabled-vs-empty distinction, conditional enforcement-event emission) is net-new. Key risks are: the disabled-vs-empty distinction needing a response-shape change beyond the repository's current `([], 0)` collapse, the readable-label snapshot needing per-emit-site resolution logic across up to 36 call sites with no existing convention to extend, and the reflective "every event type has label+category" test being inherently fragile against the plain-string-constant-class pattern already in use.

---

## 8. External References

None named by the task.
