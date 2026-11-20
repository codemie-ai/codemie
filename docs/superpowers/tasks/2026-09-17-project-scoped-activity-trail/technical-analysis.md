# Technical Research

**Task**: activity events audit trail project
**Generated**: 2026-09-17T00:00:00Z
**Research path**: filesystem

---

## 1. Original Context

EPMCDME-14325 — Project-Scoped Activity Trail

## Context
- activity_events is a single append-only table spanning three domains: project_management, user_management and budget_management. Every domain already emits events concerning a project — but each identifies the project differently, in four distinct shapes:
  - Project lifecycle (project.created/updated/deleted) uses entity_type = project, entity_id = <project_id>.
  - Personal project creation uses the same event type but sets entity_id = <user id/email> rather than a project name.
  - Project membership (user.project.assigned/removed/role_updated) uses entity_type = user, entity_id = <user_id>, and carries the project inside attributes.project_name.
  - Budget events use entity_id = <budget_id> or <assignment_id>, and in all but two emit sites do not record the project at all.
- There are 36 emit sites across 10 files, not only the budget and project services: user authentication, registration, user management, personal projects and the local auth router all emit as well.
- The read API GET /v1/admin/activity-events filters by actor_id, domain, event_type, entity_type, entity_id and time range. None of those resolve to "this project" across the three shapes above, so a project's activity cannot be assembled in one query.
- The endpoint is gated by maintainer_access_only, so project admins cannot read any of it.
- Actor name and email are resolved at read time by outer-joining the users table, and actor_id is set to null when a user is deleted. Deleting a user therefore erases the identity of everyone's past actions.
- The table was created on 2026-07-15 and ships disabled by default.
- ProjectBudgetAssignment, ProjectBudgetGroup and ProjectMemberBudgetAssignment all carry project_name, so the project is known at every budget emit site; it is simply discarded.
- A project-admin authorization concept already exists in the admin API surface (UserProject.is_project_admin) and can be reused.

## Story
As a project admin responsible for a project, I want to retrieve the complete activity record for my own project across every domain that touches it, so that I can see how the project reached its current state without escalating to a platform administrator.

## Approach
- Project attribution: dedicated indexed nullable project_name column with index on (project_name, created_at), populated explicitly at every emit site across all three domains (36 sites, 10 files). NOT a foreign key — denormalized snapshot so project deletion doesn't erase history. Reject deriving at query time via joins (assignments are soft-deleted, budgets move between projects). Reject attributes.project_name convention (unenforceable across 36 sites).
- Actor attribution: snapshot actor email and display name onto the event row at write time. actor_id keeps its FK and null-on-delete behavior; the read-time join becomes a fallback for current values rather than the only source.
- Read path: new project-scoped endpoint authorized for project admins of that project AND platform-level admins/maintainers (admin-or-maintainer), leaving the existing global endpoint untouched.
- Personal projects excluded entirely (no members/budgets, and it's the fourth inconsistent attribution shape).
- All filtering/sorting/aggregation/pagination server-side; no client-side narrowing of a broader fetched set; count computed server-side over full filtered set.

## Acceptance Criteria
1. Any activity event emitted in the context of a project stores the project on the event row in the same field for every domain/event type.
2. An event with no project context stores project field as null (not empty string/placeholder).
3. Actor's email and display name are captured on the row at write time.
4. If the actor's user account is later deleted, the event still shows who performed it (from the snapshot).
5. If a project is deleted, its historical events remain present and still identify the project by name.
6. A project admin requesting the trail for a project they administer receives that project's events from every domain, newest first, paginated.
7. A project admin requesting the trail for a project they do NOT administer is denied — no events, counts, or existence info returned.
8. A platform admin or maintainer requesting any project's trail receives it.
9. Personal project trails are not served; no personal-project event appears in any project-scoped result.
10. The existing global activity-events endpoint returns identical results/behavior after this change.
11. Filters (time range, domain, event type) apply server-side on the project trail; total count is computed server-side over the full filtered set, not derived from the returned page.
12. Pagination with ties on timestamp is deterministic — no event repeated or skipped across pages.
13. The schema migration does not modify any existing row's domain, event_type, entity_id, actor_id, or created_at value.
14. When ACTIVITY_EVENTS_ENABLED is false, a project trail request returns an explicit empty result, not an error.

## Out of Scope
- Any UI (Story 2 / EPMCDME-14326 consumes this).
- Retention policy, guaranteed audit writes, enabled-by-default decision (Story 6).
- Adding new events or new recorded values (Stories 3 and 4).
- Reworking the existing platform-admin global activity events page.

## Key Decisions
- Project rename is safe for attribution: renames are rejected via conflict if the project has any assigned users or resources, so only empty projects (no history) can be renamed.
- No backfill of pre-existing events — they carry no project and are excluded from project-scoped results.
- The project-scoped trail is a NEW endpoint, not an overload of the existing one (different authz model and filter shape).
- Project-admin authorization reuses UserProject.is_project_admin — no new permission.
- Only events explicitly about the project appear (lifecycle, membership/role changes, budget changes) — global user events like deactivation are excluded.
- "Platform administrator" = admin or maintainer throughout this story (existing global endpoint uses narrower maintainer-only guard, left unchanged).

---

## 2. Codebase Findings

### Existing Implementations

- `src/codemie/service/activity/activity_models.py` — defines `ActivityEvent` (the SQLModel table, append-only), `ActivityEventCreate` (write-side input model with `entity_type`/`entity_id` pairing validation), and the domain/event-type constant classes (`ActivityDomain`, `ActivityEntityType`, `ProjectManagementEvent`, `UserManagementEvent`, `BudgetManagementEvent`, `CustomerConfigEvent`). Current columns: `id`, `domain`, `event_type`, `entity_type`, `entity_id`, `actor_id` (FK to `users.id`, `ondelete="SET NULL"`), `attributes` (JSONB), `created_at`. No `project_name` or actor-snapshot columns exist today.
- `src/codemie/service/activity/activity_repository.py` — `ActivityEventRepository` ABC and `SQLActivityEventRepository` implementation. `insert`/`async_insert` short-circuit to `None` when `config.ACTIVITY_EVENTS_ENABLED` is `False` (this is the existing enabled-flag gate — Acceptance Criterion 14 needs an analogous read-side gate). `find_all` builds a filtered/paginated query, computes `total` via a separate `count()` query over the same `conditions` list (server-side count already established as the pattern), and enriches rows with `actor_email`/`actor_name` via an `outerjoin` on `UserDB` at read time (`ActivityEventRow` dataclass). `get_distinct_domains`/`get_distinct_event_types`/`get_distinct_entity_types`/`get_domain_mapping` back the `/filter-options` endpoint. `delete_older_than` exists for retention (out of scope here per the story).
- `src/codemie/service/activity/activity_event_service.py` — `ActivityEventService.list_events()` opens a `Session`, calls `activity_event_repository.find_all(...)`, and maps `ActivityEventRow` to the API model `ActivityEventListItem`. `get_filter_options()` is a thin passthrough to the repository's distinct-value helpers.
- `src/codemie/rest_api/routers/activity_events_router.py` — the existing global endpoint. Router prefix `/v1/admin/activity-events`, `Depends(authenticate)` at router level, and per-route `Depends(maintainer_access_only)` — the narrower maintainer-only guard the story says must remain unchanged. `list_activity_events` accepts `actor_id`, `domain` (list), `event_type` (list), `entity_type` (list), `entity_id`, `from`/`to`, `sort_dir`, `limit`/`offset`, builds `PaginationData` from `offset // limit` and `math.ceil(total/limit)`.
- `src/codemie/rest_api/models/activity_event.py` — `ActivityEventListItem`, `DomainFilterEntry`, `ActivityEventFilterOptions` Pydantic response models.
- `src/external/alembic/versions/38255069bfab_create_activity_events_table.py` — the original table-creation migration (2026-07-15). Confirms current index set: `(entity_type, entity_id)` partial index, `(actor_id, created_at)` partial index, `(domain, created_at)` index. No `project_name` index exists yet.

**36 emit sites across 10 files** (grep-confirmed `ActivityEventCreate(` occurrences):
- `src/codemie/service/project/project_service.py` — 3 sites: `PROJECT_CREATED` (`entity_id=validated_name`), `PROJECT_UPDATED` (`entity_id=project.name`), `PROJECT_DELETED` (`entity_id=project_name`). All use `entity_type=ActivityEntityType.PROJECT`.
- `src/codemie/service/project/personal_project_service.py` — 1 site: `PROJECT_CREATED` with `entity_type=PROJECT`, `entity_id=user_email` (the "personal project" shape the story calls out — project identified by user email, not project name), `attributes={"project_type": "personal"}`.
- `src/codemie/service/user/user_management_service.py` — 3 sites: `USER_CREATED`, `USER_UPDATED`, `USER_DEACTIVATED` — all `entity_type=USER`, no project context.
- `src/codemie/service/user/registration_service.py` — 1 site: `USER_CREATED`, `actor_id=None`.
- `src/codemie/service/user/authentication_service.py` — 4 sites: `USER_CREATED` (x2, different auth sources), `USER_LOGIN` (x2).
- `src/codemie/rest_api/routers/local_auth_router.py` — 1 site: `USER_LOGOUT`.
- `src/codemie/service/project/project_assignment_service.py` — 5 sites: `USER_PROJECT_ASSIGNED`, `USER_PROJECT_ROLE_UPDATED`, a bulk-assign site (`event_type` variable), and two `USER_PROJECT_REMOVED` sites (single-remove and bulk-remove paths). All are `entity_type=USER`, `entity_id=<user_id>`, and carry `attributes={"project_name": project_name, ...}` — the "project inside attributes" shape the story flags as unenforceable/inconsistent.
- `src/codemie/service/budget/budget_service.py` — 7 sites: `BUDGET_CREATED`, `BUDGET_UPDATED`, `BUDGET_DELETED` (all `entity_type=BUDGET`, `entity_id=budget_id`, no project), plus 4 `USER_BUDGET_ASSIGNED`/`USER_BUDGET_REMOVED` sites (`entity_type=USER_BUDGET_ASSIGNMENT`, `entity_id=user_id`, `attributes={"budget_id": ...}`, no project name recorded).
- `src/codemie/service/budget/project_budget_service.py` — 11 sites covering `PROJECT_BUDGET_CREATED/UPDATED/DELETED/REBALANCED/RESET`, `PROJECT_BUDGET_GROUP_CREATED/UPDATED/RESET/REBALANCED`, `MEMBER_ALLOCATION_OVERRIDDEN/OVERRIDE_CLEARED`. Only 2 of these put `project_name` into `attributes` explicitly (`PROJECT_BUDGET_CREATED` at line 994, `PROJECT_BUDGET_GROUP_CREATED` at line 1940, `PROJECT_BUDGET_GROUP_UPDATED` at line 2114 reads `group.project_name`) — matching the story's "in all but two emit sites do not record the project" observation; the rest have `project_name` available on the owning model (`ProjectBudgetAssignment`/`ProjectBudgetGroup`/`ProjectMemberBudgetAssignment` all declare `project_name` — see `src/codemie/service/budget/budget_models.py:104,212,259,306`) but discard it at emit time.
- `src/codemie/service/budget/active_project_budget_restore_service.py` — 1 site: `PROJECT_BUDGET_RESTORED`, no project name recorded, no `actor_id` passed at all (scheduler-triggered).
- `src/codemie/service/budget/inactive_project_budget_stop_service.py` — 1 site: `PROJECT_BUDGET_STOPPED`, no actor, no project name.
- `src/codemie/service/budget/inactive_cost_center_budget_stop_service.py` — 1 site: `PROJECT_BUDGET_STOPPED`, `attributes={"cost_center_name": ...}`, no project name, no actor.

### Architecture and Layers Affected

- **Data model layer**: `ActivityEvent` SQLModel (`activity_models.py`) — the append-only table definition and its indexes.
- **Migration layer**: `src/external/alembic/versions/` — Alembic is the only sanctioned path for schema changes (`.ai-run/guides/data/database-patterns.md`). The repo currently has **29 distinct branch heads** (confirmed by walking `revision`/`down_revision` across all files in `versions/`), so a new migration's `down_revision` must be chosen carefully against the actual current head(s) rather than assumed to be the activity-events-table migration.
- **Repository layer**: `SQLActivityEventRepository` (`activity_repository.py`) — `insert`/`async_insert` (write path, both sync and async session variants exist) and `find_all` (read path, filter + count + actor join). Any project-scoped query method belongs here as a new abstract + concrete method, following the existing `find_by_entity`/`find_by_actor`/`find_by_domain`/`find_all` pattern.
- **Service layer**: `ActivityEventService` (`activity_event_service.py`) — orchestration between repository and API model; a project-scoped service method would sit alongside `list_events`/`get_filter_options`.
- **API/router layer**: `activity_events_router.py` — the existing global endpoint under `/v1/admin/activity-events`, `maintainer_access_only`-gated. A new project-scoped endpoint is a separate router path per the story's decision ("NEW endpoint, not an overload").
- **Security/authorization layer**: `src/codemie/rest_api/security/authentication.py` — houses `authenticate`, `admin_access_only`, `maintainer_access_only`, `admin_or_maintainer_or_auditor_access`, and `project_admin_or_admin_user_detail_access` (an existing precedent for "admin OR project-admin-with-scoped-check" authorization, built for the user-detail endpoint in "Story 18"). This is the closest existing pattern to what the project-scoped trail's authorization dependency needs to become (admin/maintainer OR project admin of the specific project in the path/query).
- **Emit sites**: 10 files across `service/project/`, `service/user/`, `service/budget/`, and `rest_api/routers/local_auth_router.py` — every one of the 36 call sites listed above is a candidate touch point for the new project-attribution field.

### Integration Points

- `codemie.rest_api.models.user_management.UserDB` — joined at read time in `find_all` for actor email/name; would need to be paired with (or, per the story's decision, superseded at write time by) an on-row snapshot.
- `codemie.repository.user_project_repository.UserProjectRepository` — `is_admin(session, user_id, project_name) -> bool` (line 195) is the exact existing method for "is this user a project admin of this project," already used in `project_service.py:264-265` (`is_project_admin = user_project_repository.is_admin(...)`; `if not user.is_admin_or_maintainer and not is_project_admin`). This is the authorization primitive the story says to reuse (`UserProject.is_project_admin`) rather than adding a new permission.
- `can_project_admin_view_user(session, admin_user_id, target_user_id)` (`user_project_repository.py:450`) — a related but distinct existing method (checks membership overlap for a *user*, not project-scoped filtering) used by `project_admin_or_admin_user_detail_access`; illustrates the existing "project admin can see X if they administer the relevant project" authorization shape but is not directly reusable since project trail is keyed by project name, not target user.
- `config.ACTIVITY_EVENTS_ENABLED` (`src/codemie/configs/config.py:350`, default `False`) — the single global feature flag gating both the current `insert`/`async_insert` write path (in the repository) and, per Acceptance Criterion 14, needed on the new read path too.
- `codemie.clients.postgres.PostgresClient` / `get_session` — session management used throughout the service and security layers.

### Patterns and Conventions

- **ABC + single concrete implementation** for repositories (`ActivityEventRepository` / `SQLActivityEventRepository`), matching `.ai-run/guides/data/repository-patterns.md`'s "extend the matching repository" guidance — new project-scoped query methods should be added to this same class/interface pair, not a new repository.
- **Dataclass row projection** (`ActivityEventRow`) used to carry repository-layer query results (including joined columns) into the service layer before conversion to the Pydantic API model (`ActivityEventListItem`).
- **Server-side count via matching WHERE-clause list**: `find_all` builds `conditions: list` once and applies it to both the `count()` statement and the paginated `select()` statement — this is the existing, already-audited pattern for Acceptance Criterion 11 (server-side count over full filtered set).
- **Feature-flag short-circuit in the repository**, not the service or router: `insert`/`async_insert` check `config.ACTIVITY_EVENTS_ENABLED` and return `None` silently on failure (wrapped in `try/except`, logged, never raised) — the existing convention is "activity logging never breaks the caller's transaction."
- **Authorization dependencies as FastAPI `Depends`** functions in `authentication.py`, each raising `ExtendedHTTPException(code=403, ...)` with a `message`/`details`/`help` triad — any new project-scoped guard should follow this exact shape and reuse `ACCESS_DENIED_MESSAGE`/similar constants already defined in that module.
- **`project_name` denormalized on budget models already**: `ProjectBudgetGroup.project_name` is explicitly documented in-model as "cleared when the project is deleted, so the group outlives it" (`budget_models.py:202`) — i.e., the exact "denormalized snapshot, not FK" pattern the story's Approach section proposes for `activity_events.project_name` already exists elsewhere in this codebase as precedent.

---

## 3. Documentation Findings

### Guides and Architecture Docs

No guide under `.ai-run/guides/` is dedicated to activity events / audit trails. Relevant general guides:
- `.ai-run/guides/data/database-patterns.md` — directs schema changes through Alembic migrations under `src/external/alembic/versions/`, warns to "inspect Alembic state before changing migration chains" (directly relevant given the 29-head fan-out found in this repo).
- `.ai-run/guides/data/repository-patterns.md` — "Extend the matching repository" rather than adding parallel data access; relevant to adding project-scoped query methods to `SQLActivityEventRepository`.
- `.ai-run/guides/development/security-patterns.md` — "Use admin/maintainer/project-admin helper dependencies" rather than inline role checks; directly names the project-admin dependency pattern this story needs.

### Architectural Decisions

No ADR files or `DECISION:`/`ADR:` inline markers were found in the activity-events source files. `activity_models.py`'s module docstring records one design rationale inline: "Generic entity references (entity_type + entity_id) keep the table extensible to future domains without schema migrations" — this is the pre-existing design the task's four-shapes problem grew out of. `budget_models.py:202`'s comment on `ProjectBudgetGroup.project_name` ("cleared when the project is deleted, so the group outlives it") records the precedent decision for denormalized, deletion-safe project attribution that this story's Approach explicitly follows.

### Derived Conventions

- Every activity-event emit call site follows the identical call shape: construct `ActivityEventCreate(domain=..., event_type=..., entity_type=..., entity_id=..., actor_id=..., attributes=...)` then pass it plus a `session` into `activity_event_repository.insert(...)` or `.async_insert(...)`, inside the same transaction as the business mutation (per the repository module's own docstring). Any new field added to `ActivityEventCreate` (e.g., `project_name`, actor snapshot fields) will need to be threaded through all 36 call sites individually — there is no shared helper/factory that could be changed once instead.
- Sync vs. async split is per-call-site, not per-file: some services use `insert` with `Session`, others `async_insert` with `AsyncSession`, depending on the surrounding code's existing session style.

---

## 4. Testing Landscape

### Existing Coverage

- `tests/codemie/service/activity/test_activity_event_service.py` — 6 tests covering `ActivityEventService.list_events` (paginated items + total) and presumably `get_filter_options`. Uses `@patch` on `activity_event_repository`, `Session`, `PostgresClient` at the service module path.
- `tests/codemie/rest_api/routers/test_activity_events_router.py` — 8 tests covering `list_activity_events` and `get_filter_options` router functions directly (not via TestClient), patching `activity_event_service`.
- `tests/codemie/rest_api/models/test_activity_event_models.py` — 4 tests, presumably validating the Pydantic models in `models/activity_event.py`.
- `tests/codemie/service/activity/test_activity_repository.py` and `tests/codemie/service/activity/test_activity_models.py` — present but not read in detail; cover `SQLActivityEventRepository` and `ActivityEvent`/`ActivityEventCreate` respectively.
- `tests/codemie/service/budget/test_budget_service_activity.py` — dedicated file asserting budget-service emit sites fire the right event (`test_create_budget_emits_budget_created_event`, `test_update_budget_emits_budget_updated_event`, `test_assign_budget_to_user_emits_user_budget_assigned_event`, etc.), and that events are *not* emitted on provider failure (`test_create_budget_event_not_emitted_on_provider_failure`). This file is the template for the "assert project_name/actor snapshot populated" tests that project-attribution changes to `budget_service.py` and `project_budget_service.py` would need.
- `tests/codemie/service/project/test_project_assignment_service.py` — includes activity-emission assertions embedded in bulk-assign/bulk-remove tests (`test_bulk_assign_upsert_existing`, `test_bulk_remove_success`), patching `mock_activity_repo`.

### Testing Framework and Patterns

pytest with `unittest.mock.patch`/`MagicMock` for isolating repository/session/service boundaries; no integration-database test harness was observed in the files read (all activity-event tests mock the repository or session, not a real Postgres instance). Async test methods (`async def test_...`) appear in the budget-activity test file, implying `pytest-asyncio` is in use for `async_insert`-based call sites.

### Coverage Gaps

- No existing test asserts a specific `entity_id` shape per domain (i.e., nothing currently pins down "personal project creation sets `entity_id=<email>`" or "membership events put project name in `attributes`") — this task's fix to those 36 sites has no regression tests to run against before or after the change.
- No test exists for any authorization dependency analogous to what a project-scoped endpoint needs; `project_admin_or_admin_user_detail_access` (the closest precedent) was found in source but its test file was not located in this pass — the pattern's own test may need to be reused as a template rather than found ready-made.
- No migration tests were located for the `activity_events` table (Acceptance Criterion 13 — "migration does not modify any existing row's ... value" — has no existing automated check to extend).

---

## 5. Configuration and Environment

### Environment Variables

- `ACTIVITY_EVENTS_ENABLED` (`src/codemie/configs/config.py:350`, `bool`, default `False`) — the only config flag found governing this domain. Currently consulted only inside `SQLActivityEventRepository.insert`/`async_insert` (write path). No occurrence of this flag was found on any read path (service or router) in the files examined.

### Configuration Files

- `src/codemie/configs/config.py` — central Pydantic settings class where `ACTIVITY_EVENTS_ENABLED` and all other feature flags for this codebase live.

### Feature Flags and Deployment Concerns

- `ACTIVITY_EVENTS_ENABLED` is the single toggle for this domain; it ships `False` by default per the story context. No Dockerfile, CI, or deployment-manifest reference to activity events or this flag was found in this pass.

---

## 6. Risk Indicators

- The write-side `ActivityEventCreate` model change (adding `project_name` and actor-snapshot fields) fans out to all 36 emit sites across 10 files; there is no shared factory/helper to change once, so each site is an individual edit-and-verify unit, and a missed site silently produces a null-project row rather than an error.
- The repository has 29 distinct Alembic heads (confirmed by scanning `revision`/`down_revision` pairs across `src/external/alembic/versions/`) — choosing the wrong `down_revision` for a new column migration risks either a silent branch or a merge migration being required; `.ai-run/guides/data/database-patterns.md` explicitly warns to inspect Alembic state before changing the chain.
- `find_all`'s existing actor-join (`outerjoin(UserDB, ActivityEvent.actor_id == UserDB.id)`) sets a precedent that must be reconciled with the story's actor-snapshot decision — introducing snapshot columns changes what `ActivityEventRow`/`ActivityEventListItem` should prefer (snapshot vs. live join), and the existing global endpoint's read behavior must stay identical (Acceptance Criterion 10) while the new project endpoint uses different logic.
- No dedicated authorization-dependency test file was located for `project_admin_or_admin_user_detail_access` in this pass, despite it being the closest existing precedent for the new endpoint's "admin-or-project-admin" guard — the authorization pattern this story needs may be under-tested even where it already exists.
- Personal-project exclusion (Acceptance Criterion 9) depends on being able to distinguish a personal-project row from a real-project row after the project-attribution fix; currently personal-project creation is emitted with `entity_id=user_email` and no other structural marker beyond `attributes={"project_type": "personal"}` (`personal_project_service.py:89`) — any project-scoped query must not accidentally match on this shape.
- Two of the three budget-scheduler emit sites (`active_project_budget_restore_service.py`, `inactive_project_budget_stop_service.py`) pass no `actor_id` at all (scheduler-triggered, not user-triggered) — an actor-snapshot change must handle a `None` actor gracefully at those sites, not assume a resolvable user.
- No migration-level regression test currently exists to enforce Acceptance Criterion 13 ("migration does not modify existing row values") — this would need to be a new test, not an extension of one that already exists.

---

## 7. Summary for Complexity Assessment

This task touches four architectural layers in the existing `codemie` backend: the data model (`ActivityEvent` SQLModel and a new Alembic migration for the added columns), the repository layer (`SQLActivityEventRepository` — new project-scoped query method plus, per the story, a snapshot-based actor field to reconcile with the existing read-time `UserDB` outerjoin), the service layer (`ActivityEventService` — a new project-scoped orchestration method), the API layer (a brand-new router/endpoint separate from the existing `/v1/admin/activity-events`), and the security layer (a new or adapted authorization dependency alongside the existing `maintainer_access_only`/`admin_access_only`/`project_admin_or_admin_user_detail_access` family). The change surface for project attribution alone spans 36 individually-verified `ActivityEventCreate(...)` call sites across 10 files in `service/project/`, `service/user/`, `service/budget/`, and one router, each needing an explicit, correct project-name value (or explicit `None`) threaded through — this is breadth-heavy rather than depth-heavy work, with no single choke point to change once.

Technical novelty is moderate: the closest precedent for "admin OR project-admin-scoped-to-a-specific-project" authorization already exists (`project_admin_or_admin_user_detail_access` plus `UserProjectRepository.is_admin`), and the closest precedent for "denormalized project_name snapshot that survives project deletion" already exists on `ProjectBudgetGroup`/`ProjectBudgetAssignment`. Neither pattern is being invented from scratch, but assembling them together for a new endpoint with different filter/pagination/authorization shape than the existing global endpoint is new integration work, not a copy-paste. The 29-way Alembic head fan-out in this repository is an operational risk for the migration step regardless of the schema change's own simplicity.

Test coverage posture is uneven: the activity-events subsystem itself (model, repository, service, router) has direct unit tests, and `test_budget_service_activity.py` demonstrates the pattern for asserting emit-site behavior that this task's fixes to 36 sites would need to extend broadly. However, no test currently pins down the four inconsistent identification shapes this story is correcting, no authorization-dependency test was found for the closest existing precedent this task would extend, and there is no existing migration-safety test for Acceptance Criterion 13. Key risk factors are therefore: breadth of emit-site changes (36 sites/10 files) with no shared abstraction to reduce that count, migration-chain correctness given many existing heads, and reconciling a new actor-snapshot write path with the existing read-time join without breaking the untouched global endpoint (Acceptance Criterion 10).

---

## 8. External References

None named by the task. The ticket is self-contained (a Jira-style story with Context/Story/Approach/Acceptance Criteria/Out of Scope/Key Decisions sections) and does not point to any file, directory, or URL outside this repository as a source of truth.
