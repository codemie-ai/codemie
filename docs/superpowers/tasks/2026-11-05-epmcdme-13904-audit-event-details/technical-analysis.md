# Technical Research

**Task**: activity audit-events project-management budget-management
**Generated**: 2026-11-05
**Research path**: filesystem

---

## 1. Original Context

Jira EPMCDME-13904 "Add logging coverage for project and budget management". It was implemented and merged (activity events: domains project_management / budget_management, event types like project.updated, budget.updated, budget.project_budget.deleted/updated, budget.project_budget_group.created/updated), but QA reopened it.

Original acceptance criteria:
1. Project management operations have logging coverage comparable to the existing user management logging approach.
2. Budget management operations are covered with logging.
3. Logs are created for successful project management actions.
4. Logs are created for successful budget management actions.
5. Logs include enough context to support troubleshooting and auditability, including the action type and affected project or budget entity where applicable.
6. Logging does not expose sensitive data.
7. Existing user management logging behavior is not regressed.
8. Logging coverage is validated for the main project management and budget management flows.

QA reopen comment (with screenshots of the "Activity events" UI; the details modal shows fields When, Domain, Event type, Entity type, Entity ID, Actor, Actor ID, Attributes):
1. Enabling/disabling project member enforcement is logged, but it's not clear what happened — it shows only `project.updated` with `Attributes: —` (entity project / logging_test_proj).
2. Updating budget soft limit, hard limit, reset period is not shown either — `budget.updated` events (entity budget / codemie-cli-200, autotest-act-105b89e0) have `Attributes: —`.
3. Changing budget type is also not reflected in details (`budget.updated`, Attributes: —).
4. For project budgets the same: records are created (budget.project_budget.deleted, budget.project_budget.updated, budget.project_budget_group.updated) but there are no details about the operations. Only budget.project_budget_group.created showed an attribute (project_name).

User request: identify places where new log lines / event details are needed and add them. Goal of this follow-up: every project/budget management activity event carries attributes that describe what changed (e.g. changed field names with old→new values for non-sensitive fields such as member enforcement flag, soft/hard limits, reset period, budget type/category, project budget amounts), and any project/budget management flows that currently emit no activity event or no logger line get one. Must not leak sensitive data.

---

## 2. Codebase Findings

### Existing Implementations

**Purpose A — persist an audit event with attributes.**
- `ActivityEvent` / `ActivityEventCreate` — `src/codemie/service/activity/activity_models.py`. `attributes: Optional[Dict[str, Any]]` stored as `JSONB NULL`; no schema, size limit, or redaction. Constants: `ActivityDomain`, `ActivityEntityType`, `ProjectManagementEvent` (created/updated/deleted/ownership_changed), `BudgetManagementEvent` (20 types; **no `PROJECT_BUDGET_GROUP_DELETED`**). Validator: entity_type/entity_id both set or both None.
- `activity_event_repository.insert` / `async_insert` — `src/codemie/service/activity/activity_repository.py:138,157`. No-op unless `config.ACTIVITY_EVENTS_ENABLED` (`src/codemie/configs/config.py:388`, default `False`); writes in a nested savepoint inside the caller's transaction; swallows exceptions (logs error). `ActivityEvent(**event.model_dump())` — attributes passed through verbatim.
- Read side: `ActivityEventService.list_events` (`activity_event_service.py`) returns `attributes` verbatim; API model `src/codemie/rest_api/models/activity_event.py:32` `attributes: dict[str, Any] | None`.

**Purpose B — reference pattern (user management).** `UserManagementService.update_user` — `src/codemie/service/user/user_management_service.py:359-390`: `attributes=dict(fields) or None` — flat dict of **new values only** (no old values), built by `_build_updates_dict` (`:1398`) from name, picture, email, user_type, is_admin, is_maintainer, is_auditor, project_limit. So the reference records `email`/`name` in attributes. Logger line: `user_updated: actor_user_id=…, target_user_id=…, is_admin=…, domain=user_management`. Ownership change (`project_service.py:374`) is the only project/budget site that already records before/after (`previous_owner_username`, `new_owner_username`).

**Purpose C — every project/budget management emit site and what it passes today.**

| Site | Event | entity_type / entity_id | attributes today |
|---|---|---|---|
| `project_service.py:242` create_shared_project_in_session | project.created | project / name | none |
| `personal_project_service.py:82` | project.created | project / **user_email** | `{project_type: personal}` |
| `project_service.py:322` update_project | project.updated | project / name | **none** |
| `project_service.py:374` update_project_ownership | project.ownership_changed | project | previous/new owner username |
| `project_service.py:700` delete_project | project.deleted | project | affected_budgets/groups/integrations ids |
| `budget_service.py:283` create_budget | budget.created | budget / budget_id | **none** |
| `budget_service.py:375` update_budget | budget.updated | budget / budget_id | **none** |
| `budget_service.py:751` sync_budgets_from_provider | budget.deleted | budget | `{reason: absent_from_provider}` |
| `budget_service.py:1160,1199,1294,1307` user assignment | budget.user_assignment.created/deleted | user_budget_assignment / user_id | budget_id and/or category |
| `project_budget_service.py:1021` create_project_budget | budget.project_budget.created | **project_budget_group** / budget_id | `{project_name}` |
| `project_budget_service.py:1360` update_project_budget | budget.project_budget.updated | project_budget_group / budget_id | `{updated_fields: [names]}` (no values) |
| `project_budget_service.py:1406` rebalance | budget.project_budget.rebalanced | budget | budget_category, project_name |
| `project_budget_service.py:1520` reset | budget.project_budget.reset | budget | budget_category, project_name |
| `project_budget_service.py:1584` override_member_allocation | member_allocation.overridden | project_budget_group | user_id, allocated_max/soft (new only; `override_reason` not recorded) |
| `project_budget_service.py:1671` clear_member_override | member_allocation.override_cleared | project_budget_group | user_id |
| `project_budget_service.py:1847` delete_project_budget | budget.project_budget.deleted | project_budget_group / budget_id | **none** |
| `project_budget_service.py:2050` create_project_budget_group | project_budget_group.created | group / group.id | `{project_name}` |
| `project_budget_service.py:2224` update_project_budget_group | project_budget_group.updated | group / group_id | `{project_name}` only |
| `project_budget_service.py:2467,2503` group reset/rebalance | group.reset/rebalanced | group | project_name, category_count |
| stop/restore services (`inactive_*_stop_service.py`, `active_project_budget_restore_service.py`) | stopped/restored | — | not inspected in detail |

**Flows with no activity event today** (searched every `activity_event_repository` call in `service/project`, `service/budget`, `cost_center_service.py`, `routers/project_settings.py`):
- `update_project_budget_group` early returns at `project_budget_service.py:~2170` (scalar-only: name/description/budget_duration) and `~2193` (notification-only) — logger.info only, **no event**.
- `delete_project_budget_group` (`project_budget_service.py:2425-2446`) — logger.info only; child `project_budget.deleted` events are emitted per category via `delete_project_budget`. Prior spec explicitly deferred this (`docs/superpowers/tasks/2026-08-04-epmcdme-13904-add-logging-project-budget/spec.md:20`).
- `update_allowed_models` / `reset_allowed_models` (`project_service.py:819,846` → `_persist_allowed_models:900`) — no event, no success logger.info (only `logger.exception` on failure).
- `CostCenterService` create/update/delete (`cost_center_service.py:111,137,163`) — logger.info only.
- `routers/project_settings.py:124,198,235` — logger.info only (project integrations).

**Other touched code.**
- `SettingsService.set_enforce_member_spend_limits` / `get_enforce_member_spend_limits` — `src/codemie/service/settings/settings.py:1136,1172`. The enforcement flag lives in a project `Settings` row (alias `ENFORCE_MEMBER_SPEND_LIMITS_ALIAS`), not on `Application`. Sole writer caller: `project_service.py:318`.
- `application_repository.update_project` — `src/codemie/repository/application_repository.py:758`: mutates the passed `Application` in place; **unconditionally assigns** `display_name` and `cost_center_id`.
- `budget_repository.update` — `src/codemie/repository/budget_repository.py:145`: `get_by_id` + `setattr` + refresh on the session-identity instance.
- Routers: `routers/projects/management.py:47` PATCH `/projects/{name}` (and `/allowed-models`); `routers/budget_router.py:144` PATCH `/budgets/{id}` (no DELETE endpoint for global budgets); `routers/project_budget_router.py` PATCH/DELETE `/{budget_id}`, reset, rebalance, member override, group create/update/delete (`:522,571,593`).

### Decisions Already in the Code
- Attributes are free-form JSONB, intentionally unvalidated — `docs/superpowers/specs/2026-07-15-activity-events-design.md:113,305` ("Domain-specific payload: before/after state, extra context"; per-event schema validation out of scope).
- Emission is best-effort and inside the caller's transaction (savepoint; failure logged, not raised) — `activity_repository.py:138-175`.
- In-place mutation: `budget_repository.update` and `application_repository.update_project` mutate the same object the service already holds, so `budget`/`project` after the call carries new values; old values are only available if read before the call. Alternative (re-query) not used anywhere.
- Enforcement flag stored as a string `"true"/"false"` in a Settings row, read via a separate query — not on the project row.
- Group update fans out: `_update_group_categories` calls `update_project_budget` per category via `_SimpleUpdateRequest` (`:2520`; always sets max+soft, duration only if changed) and `delete_project_budget` for `pct<=0`. One group PATCH therefore yields N `project_budget.updated`/`.deleted` events plus one `group.updated`.
- `_collect_updated_group_fields` (`:2082`) omits `soft_limit_notification_enabled`; `_changed_project_budget_fields` (`:689`) omits `models`, so a models-only update yields `updated_fields: []`.
- entity_type for single project budgets is `project_budget_group` while entity_id is a budget_id (created/updated/deleted/override); rebalance/reset use `budget`. No `PROJECT_BUDGET` entity type constant exists.
- Logger convention in budget services: `budget_event=<name> component=<svc> key=value …`; project service: `project_updated: project=…, by=…, domain=project_management`.

### Task Statements Checked Against the Code
- "project.updated with Attributes: —" — holds (`project_service.py:322`, no attributes).
- "budget.updated … Attributes: —" — holds (`budget_service.py:375`). Logger already logs `updated_fields` names (`:370`).
- "Changing budget type … not reflected" — `BudgetUpdateRequest` (`rest_api/models/budget.py:52`) has no `type` field; `budget_category` is the only type-like updatable field (holds for that). UI label mapping not verified.
- "budget.project_budget.updated … no details" — contradicted for code on this branch: `:1360` passes `{updated_fields: [...]}` since EPMCDME-13275 (`919f94bd4`). Holds that no values are recorded; `[]` for models-only updates. QA environment version unknown.
- "budget.project_budget_group.updated … no details" — partly contradicted: passes `{project_name}` but nothing about the change.
- "budget.project_budget.deleted … no details" — holds (`:1847`).
- "Must not leak sensitive data" — no redaction mechanism exists in the activity package; reference pattern (`update_user`) stores `email`/`name` in attributes. Candidate sensitive fields in these flows: `notification_owner_email`, free-text `description`, `override_reason`. No API keys flow through these update paths (LiteLLM creds go via `upsert_project_litellm_creds_by_alias`, not touched).

### Consumers of Changed Contracts
- `ActivityEvent.attributes` readers: `ActivityEventService.list_events` → `activity_events_router.py` → UI `ActivityEventsPage.tsx`. Searched `attributes` across `src/codemie` activity/analytics: no backend code filters or aggregates on attribute keys (`routers/analytics/activity.py` references the domain constants, not attributes).
- UI (`codemie-ui/src/pages/settings/administration/ActivityEventsPage.tsx:108-136,418-424`): table "Details" cell renders each top-level key as `key: String(v)` truncated at `max-w-[100px]` — nested objects render as `[object Object]`, arrays as comma-joined; details modal renders `JSON.stringify(attributes, null, 2)` in a scrollable `max-h-64` pre, or `—` when empty/null. Type: `Record<string, unknown> | null` (`src/types/entity/activityEvent.ts`).
- Tests asserting exact `attributes` equality (would break if keys added to these sites): `test_budget_service_activity.py:300,356,404,514`; `test_project_service_delete_update.py:504`; `test_project_service_ownership.py`. `test_update_budget_emits_budget_updated_event` (`:182`) does not assert attributes.

### Architecture and Layers Affected
Service layer: `ProjectService`, `BudgetService`, `ProjectBudgetService` (incl. group helpers); model layer: `activity_models.py` constants. Routers delegate entirely to services. Repository layer unchanged by the existing pattern.

### Integration Points
Budget services sync to LiteLLM via `get_active_provider()`; events are emitted after provider sync succeeds and before the router commits. `SettingsService` (sync session, separate query) for the enforcement flag; `update_project` uses sync `get_session()`, budget services use `AsyncSession`.

### Patterns and Conventions
Direct `activity_event_repository.(async_)insert(ActivityEventCreate(...), session)` at the end of each service method, preceded or followed by a structured `logger.info`. No helper/builder for attributes or diffs exists.

---

## 3. Documentation Findings

### Guides and Architecture Docs
`.ai-run/guides/` exists (quality-gates, testing-patterns, code-quality, logging-patterns); none covers activity events. `.ai-run/guides/development/logging-patterns.md:5`: "avoid leaking secrets or tokens" is the only sensitive-data rule (no PII/email guidance). Design: `docs/superpowers/specs/2026-07-15-activity-events-design.md`. Prior run: `docs/superpowers/tasks/2026-08-04-epmcdme-13904-add-logging-project-budget/` (spec, plan, qa-report).

### Architectural Decisions
Activity events are append-only, action-shaped (not state snapshots); attributes carry "before/after state, extra context" by design. Prior 13904 spec put `delete_project_budget_group` event out of scope.

### Derived Conventions
Flat attribute dicts with snake_case keys; ids/names rather than objects; `previous_*`/`new_*` naming in the only before/after example.

---

## 4. Testing Landscape

### Existing Coverage
- `tests/codemie/service/budget/test_budget_service_activity.py` — create/update budget, user assignment, create/delete project budget events.
- `tests/codemie/service/budget/test_project_budget_service_lifecycle.py`, `test_project_budget_service.py`, `test_project_budget_service_group_creation.py` — project budget/group flows (logger coverage from prior 13904).
- `tests/codemie/service/project/test_project_service.py:459` (created event), `test_project_service_delete_update.py:482,1026,1104-1160` (deleted/updated with mocked activity repo), `test_project_service_ownership.py`.
- `tests/codemie/service/activity/` — models, repository, service.
- `tests/codemie/rest_api/routers/test_budget_router_auditor.py` — auditor read access only; no activity assertions.

### Testing Framework and Patterns
pytest + pytest-asyncio; `@patch("codemie.service.<module>.activity_event_repository")` with `AsyncMock`/`MagicMock`, then `call_args[0][0].attributes == {...}`. Repos mocked so `get_by_id` and `update` may return different objects.

### Coverage Gaps
No tests assert attributes for project.updated, budget.updated, project_budget.updated/deleted, or group.updated; no tests for group update early-return paths emitting events; none for group delete event or allowed-models logging.

---

## 5. Configuration and Environment

### Environment Variables
`ACTIVITY_EVENTS_ENABLED` (`configs/config.py:388`, default False) gates all inserts.

### Configuration Files
`budget_config.predefined_budgets` — predefined budgets reject updates (403) before any event.

### Feature Flags and Deployment Concerns
`customer_config.is_feature_enabled("projectChargeback")` gates chargeback fields in `update_project`. No migration involvement for attributes (JSONB). Commands: `make ruff` (format + check), `make license-check`, `poetry run pytest tests/<path>`; AGENTS.md: run/write tests only when requested.

---

## 6. Risk Indicators

- In-place mutation in `budget_repository.update` / `application_repository.update_project` means any value read from `budget`/`project` after the update is the new value (see Decisions).
- `application_repository.update_project` assigns `display_name`/`cost_center_id` unconditionally, so "provided" vs "actually changed" differs for those fields.
- Enforcement flag is outside the project row (Settings), read via a separate query.
- Group PATCH emits N child `project_budget.updated`/`deleted` events plus the group event; scalar-only and notification-only group updates emit none.
- `delete_project_budget_group` has no event and no `PROJECT_BUDGET_GROUP_DELETED` constant.
- Sensitive-data rule is uncodified; `notification_owner_email`, `description`, `override_reason` pass through these flows, and the reference user pattern stores email.
- UI table cell shows `[object Object]` for nested attribute values and truncates at 100px; modal shows full JSON.
- Tests asserting exact `attributes` equality (Consumers subsection) break if keys are added at those sites.
- QA observation on `project_budget.updated` / `group.updated` does not match branch code; deployed QA build unverified.
- entity_type `project_budget_group` used for single project-budget ids.

---

## 7. Summary for Complexity Assessment

The change sits in the service layer of three services — `ProjectService.update_project` (plus allowed-models update/reset), `BudgetService.update_budget`/`create_budget`, and `ProjectBudgetService` (update/delete project budget, group update incl. two early-return paths, group delete) — plus possibly a new constant in `activity_models.py`. All sites already call `activity_event_repository` with the same `ActivityEventCreate` shape; the gap is the `attributes` payload, which is free JSONB with no schema, migration, or backend consumer that parses keys. About 8-12 emit sites need payload changes; 3-4 flows emit nothing today.

Novelty is low but not zero: no diff helper exists, and the repositories mutate objects in place, so before-values have to be captured before the update calls. The enforcement flag is stored in a Settings row rather than on the project. Group updates fan out into per-category child events. The only before/after example in the code is ownership change (`previous_*`/`new_*`). The user-management reference records new values only, including email.

Test posture: the activity tests use a consistent mock-repo pattern and are easy to extend. Several existing tests assert exact attribute dicts and need updating where keys change. No tests cover attributes on the update/delete events QA flagged. The UI has rendering limits: the table cell uses `String(v)` with 100px truncation, while the modal shows the full JSON. The sensitive-data boundary for the email and free-text fields is a design question that the code does not answer.

---

## 8. External References

`codemie-ui` (named in task as read-only reference): `C:/Users/KostiantynPshenychny/Documents/epm-cdme/repos/codemie-ui` resolved.
- `src/types/entity/activityEvent.ts`: `attributes: Record<string, unknown> | null`.
- `src/pages/settings/administration/ActivityEventsPage.tsx:108-136`: table cell — if `Object.keys(attributes).length > 0`, one chip per top-level key: `{k}:` + `String(v)` in `truncate max-w-[100px]`; else "View details".
- Same file `:418-424`: modal "Attributes" — `JSON.stringify(selectedEvent.attributes, null, 2)` in `pre … overflow-auto max-h-64 whitespace-pre-wrap break-all`; `—` when null or empty object.
