# Technical Research

**Task**: personal project activity event project.created entity_id mixed-case email
**Generated**: 2026-11-16
**Research path**: filesystem

---

## 1. Original Context

EPMCDME-17316 (Bug, Jira) — Mixed-case email user: project.created activity event not found by entity_id (personal_project_service.py).

Description: For users whose email contains uppercase letters, querying activity events with event_type=project.created and entity_id=<email> returns zero results. The change notes say this event should now carry the canonical (real) mixed-case personal-project name as entity_id, not the raw email. The event cannot be found under either value for these users, so audit and activity views cannot find the personal-project creation event. Lowercase-email users are unaffected.
Preconditions: preview stand; a user created with an email containing uppercase letters (e.g. AutoTest.Mixed.<hex>@Email.com); the personal project for that user is created under the same mixed-case name; a control user with a lowercase email created the same way.
Steps: (1) create a user whose email contains uppercase letters; personal project is created under the same mixed-case name. (2) Query activity events with event_type=project.created and entity_id=<the email>. (3) Repeat for a lowercase-email user.
Expected: at least one project.created event exists with entity_id equal to the real personal-project name (canonical mixed-case), not the raw email; the query in step 2 returns >= 1 event.
Actual: the query returns 0 events (automated check: "A project.created event must exist for the real mixed-case project name. Expected: >= 1, but was: 0."). Control lowercase-email user returns 1 event. Membership/default-project behaviour is covered by a separate passing UI check and is out of scope.
Affected areas: personal_project_service.py (PROJECT_CREATED emission, entity_id at line 90, related logic at lines 195, 268, 281, 286); activity/audit event recording for personal-project creation; activity-event queries filtering by entity_id.
Acceptance criteria: 1. For a mixed-case-email user a project.created activity event is recorded with entity_id equal to the real (canonical mixed-case) personal-project name. 2. Querying event_type=project.created and entity_id=<real mixed-case project name> returns >= 1 event for such a user. 3. Lowercase-email users unchanged (regression). 4. tests/user/test_default_project_api.py::test_mixed_case_email_user_gets_one_membership_and_one_default_project (in the test-harness repo, not this one) passes without the xfail(strict=True) marker.
Related: EPMCDME-16566 (merged into main as 7cd24080e1: personal-project matching made case-insensitive, membership uses the application's real name, event entity_id uses the returned real name).

(Orchestrator hints: ensure_personal_project_async emits PROJECT_CREATED only after _create_personal_project and returns early when _has_personal_project_complete; other emitter project_service.py ~274; find which flow the stand uses, how email is stored, how the events query filters, ACTIVITY_EVENTS_ENABLED, swallowed insert failures.)

---

## 2. Codebase Findings

Checkout: `D:/Projects/codemie-17316`, branch `EPMCDME-17316_follow-up`; `7cd24080e1` (EPMCDME-16566) is an ancestor of HEAD, so the "canonical name" change is already in this tree. Python / FastAPI / SQLModel / Postgres; pydantic 2, email-validator 2.3.

### Existing Implementations

**Purpose A: record one `project.created` audit event for a user's personal project, keyed by the project's real name.**
- `src/codemie/service/project/personal_project_service.py:46-111` `ensure_personal_project_async` — opens an isolated async session; returns True with NO event when `_has_personal_project_complete` (line 76); else `_create_personal_project` (81) and `activity_event_repository.async_insert(... entity_id=project_name ...)` (85-95, line 90), commit (98). Any exception -> logged, returns False (103-111), whole isolated transaction lost.
- `_create_personal_project` (208-291) returns the application's real name (`existing_app.name` line 268 or `application.name` line 278); membership is written under that name (281-286). Already canonical; no email-derived value reaches `entity_id`.
- `_find_application` (189-195) -> `application_repository.aget_active_by_name_case_insensitive` (`repository/application_repository.py:866`, exact-case name ordered first, non-deleted). `_is_reusable` (198-205) at 203 compares `application.name == user_email` exactly, otherwise requires personal + same creator.
- `src/codemie/service/activity/activity_repository.py:187-207` `async_insert` — no-op returning None when `config.ACTIVITY_EVENTS_ENABLED` is False (default `False`, `configs/config.py:447`); flushes caller's writes, then inserts inside `begin_nested()`; any insert exception is caught, logged `[activity_events] async_insert failed ...`, and swallowed (returns None, personal project still commits). `async_insert_strict` (209) propagates instead; not used here.
- Unit tests pin the canonical name: `tests/codemie/service/project/test_personal_project_service.py:1242` (`test_ensure_uses_canonical_name_for_activity_event`, mocks `_create_personal_project`), `:1304` (no event when mixed-case project+membership already exist), `:1324`.

**Purpose B: read events back filtered by event type and entity.**
- `src/codemie/rest_api/routers/activity_events_router.py:53-96` `GET /v1/admin/activity-events` (maintainer only) -> `activity_event_service.list_events` -> `activity_repository.py:292-345` `find_all`: `event_type IN (...)`, `entity_type IN (...)`, `entity_id == :value` (line 324) — exact, case-sensitive equality, no lowercasing, no default time window (from/to optional), default limit 50. `entity_id` column is `Text` (`activity_models.py:138`); index `(entity_type, entity_id)`.
- `find_by_entity` (235-252) also exact equality.

**Purpose C: who calls `ensure_personal_project_async` (which flow created the stand's user + project).**
- Local login `authentication_service.py:787-788` (`authenticate_and_login`, email = `user.email` loaded from DB).
- Every persistent-auth request `authentication_service.py:395-396` `_finalize_authentication` (email = security user's stored email); reconcile on email change `:583-588` -> `personal_project_service.py:114-156` (case-only change is a no-op, line 131).
- Registration `registration_service.py:215,252` (email = `user.email` as persisted).
- `analytics/cli_analytics_project_gate.py:249`, `enterprise/migration/coordinator.py:104`.
- Admin user creation (`user_management_router.py:211` -> `user_management_service.py:199-268`, `793+`) creates the user only and emits `user.created`; it does NOT create a personal project. The project appears on the user's first login/request.
- First IdP login `authentication_service.py:182-247`: stores `email=idp_user.email or username` as given, then `_ensure_projects_exist(project_names + [db_user.email])` (250-270, 242) pre-creates a plain (shared, created_by None) Application with `aget_or_create`; `ensure_personal_project_async` then converts it (event emitted).

**Other `PROJECT_CREATED` emitter:** `project_service.py:271-280` (`create_shared_project_in_session`, `entity_id=validated_name`, actor = creator) — shared projects only; test at `tests/codemie/service/project/test_project_service.py:500`.

**Other touched code:** `rest_api/security/user.py:31` `same_project_name` (casefold equality), `:99` `resolve_membership_default`; `rest_api/models/user_management.py:169,222,225` `RegistrationRequest.email` / `UserCreateRequest.email` typed `EmailStr`; `LoginRequest.email` (185) is plain `str`.

### Decisions Already in the Code
- Event `entity_id` = the application's real stored name (`personal_project_service.py:90`, set by 16566, spec `docs/superpowers/tasks/2026-10-29-epmcdme-16566-personal-project-case-insensitive/spec.md` "The activity event `entity_id` uses the canonical name"). Alternative: raw email. Reason recorded in that spec.
- Personal-project name = the stored `users.email` string, not the email as typed by the caller (`ensure_...` is always called with the DB/IdP value). Stored form is whatever the creating flow persisted.
- Local-mode admin creation and registration persist the value after pydantic `EmailStr` validation: `UserCreateRequest.email` / `RegistrationRequest.email` -> `create_local_user(email=...)` (`user_management_service.py:241-245`) and `register_user` (`registration_service.py:89-91`) store it without further normalization. **Verified with the repo venv (`D:/Projects/codemie/.venv`, email-validator 2.3.0, pydantic): `EmailStr` turns `AutoTest.Mixed.ab12@Email.com` into `AutoTest.Mixed.ab12@email.com`** (domain lowercased, local part kept). User lookup by email is case-insensitive (`user_repository.py:80,608`), so login with the typed address still resolves the user.
- Event writes are best-effort by convention (`async_insert` swallows; `_strict` variants exist for flows that must not lose the event, e.g. `share_link_service.py:147`).
- Event queries match `entity_id` exactly; audit lookups are case-sensitive by design (no recorded reason).
- No code path lowercases the email before it becomes a project name (grep of `lower()`/`casefold` in `service/user`, `repository/user_repository.py` shows lookups only).

### Task Statements Checked Against the Code
- "event entity_id should be the canonical mixed-case name, not the raw email" — holds in code: `personal_project_service.py:85-95` passes `_create_personal_project`'s return value; unit-tested (`test_personal_project_service.py:1242`).
- "ensure_personal_project_async emits PROJECT_CREATED only after _create_personal_project and returns early when complete" (hint) — holds (lines 76-78, 81-95).
- "lines 195, 268, 281, 286 related logic" — holds: 195 lookup, 268 `existing_app.name`, 281/286 membership by real name.
- "Event cannot be found under either value for mixed-case users" — NOT reproducible by reading code: for a new user whose personal project is created through `ensure_personal_project_async` with `ACTIVITY_EVENTS_ENABLED` on, the insert uses the application's stored name for both case classes. Unverified against the stand (no stand/harness access; harness repo `tests/user/test_default_project_api.py` is outside this repository).
- "personal project is created under the same mixed-case name [as the email typed]" — contradicted for the admin-create/registration path: the stored email (and thus project name) has a lowercase domain (see Decisions; `AutoTest.Mixed.x@Email.com` -> `...@email.com`). Unverified for the stand's actual creation flow (IdP/Keycloak, which stores the email as delivered at `authentication_service.py:204`).
- "Lowercase-email users unaffected / control returns 1 event" — consistent with the EmailStr finding (a lowercase address is unchanged by normalization, so typed == stored == event entity_id).
- "ACTIVITY_EVENTS_ENABLED" — default False (`config.py:447`); no stand value readable here (`config/`, `deploy-templates/`, `docker-compose.yml` hold no `ACTIVITY_EVENTS` entry); control user's event shows it is on at the stand.
- "Related: 7cd24080e1 merged" — holds (ancestor of HEAD). Whether the preview stand runs a build containing it is unverified.

### Consumers of Changed Contracts
Contract in question: `entity_id` of personal `project.created` events (and the stored personal-project name it carries).
- Readers of event `entity_id`: `GET /v1/admin/activity-events` filter (`activity_repository.py:324`, exact match); `find_by_entity` (`:247`); UI/audit consumers of the same endpoint. Any change of what is stored changes what existing exact-match queries find; existing rows keep their old value (append-only table, `activity_models.py:128`).
- Writers sharing the shape: `project_service.py:276` (shared projects, name as validated); no other personal-project emitter (grep `PROJECT_CREATED` over `src`: two sites).
- Name consumers if the stored email/project name changed: `user_projects.project_name` membership rows (`user_project_repository`), `resolve_membership_default` (`rest_api/security/user.py:99`), budget/LiteLLM customer id keyed by `user.email` (16566 spec section 3), `cli_analytics_project_gate.py:245-249`.
- Callers of `ensure_personal_project_async`: listed under Purpose C; all pass the stored email.

### Architecture and Layers Affected
Service (`PersonalProjectService`), repository (`ApplicationRepository`, `ActivityEventRepository`), REST models (`user_management.py` DTO validation), REST router (activity events read API), user/auth services that feed the email.

### Integration Points
Postgres (applications, user_projects, activity_events); IdP/Keycloak claims for persistent mode; LiteLLM budget keyed by email (not touched by the event).

### Patterns and Conventions
Static service + module singleton; sync/async repository twins; isolated async session for personal-project creation (non-blocking, returns bool); no email in logs (use user_id); activity events via `ActivityEventCreate` + `ActivityDomain`/`ProjectManagementEvent`/`ActivityEntityType` enums.

---

## 3. Documentation Findings

### Guides and Architecture Docs
`.ai-run/guides/` exists (AGENTS.md index: API, data, development, testing). A grep for `project.created` / `activity events` in the guides returned nothing. Relevant prior art: `docs/superpowers/tasks/2026-10-29-epmcdme-16566-personal-project-case-insensitive/{spec,technical-analysis}.md`.

### Architectural Decisions
16566 spec: personal-project name is created with the given email unchanged; "Normalising IdP emails or renaming existing applications" is an explicit non-goal; event entity_id uses canonical name; no data migration.

### Derived Conventions
Case-insensitive matching for identity decisions (`same_project_name`, `aget_active_by_name_case_insensitive`, `user_repository.get_by_email`); exact matching for access checks and for activity-event `entity_id` filtering.

---

## 4. Testing Landscape

### Existing Coverage
- `tests/codemie/service/project/test_personal_project_service.py` — ensure/create/reconcile incl. mixed-case cases (1150-1340), all with mocked repositories/sessions.
- `tests/codemie/service/activity/test_activity_repository.py`, `test_activity_repository_sqlite.py` — insert enabled/disabled, swallow behaviour.
- `tests/codemie/service/project/test_project_service.py:500` — shared `project.created`.
- `tests/codemie/service/user/test_registration_service.py` (EmailStr hit).

### Testing Framework and Patterns
pytest + pytest-asyncio; `patch("codemie.service.project.personal_project_service.<repo>")`, `_make_async_session_cm(AsyncMock())`; `monkeypatch.setattr(config, "ACTIVITY_EVENTS_ENABLED", True)` pattern in permission/assistant share tests. Only tests run when explicitly asked (AGENTS.md).

### Coverage Gaps
- No test drives `ensure_personal_project_async` against a real/in-memory DB with a mixed-case email and then reads the event back via `find_all` / the router.
- No test of `UserCreateRequest`/`RegistrationRequest` email normalization into the personal project name or the event.
- No router-level test of `entity_id` filtering with mixed case.
- The failing check lives in the test-harness repo (not available here).

---

## 5. Configuration and Environment

### Environment Variables
`ACTIVITY_EVENTS_ENABLED` (bool, default False, `configs/config.py:447`), `ACTIVITY_EVENTS_RETENTION_DAYS` (90; daily purge job `rest_api/main.py:816-867`, only when enabled). `IDP_PROVIDER` ("local" gates admin user creation, `user_management_router.py:211`), `ENABLE_USER_MANAGEMENT`, `EMAIL_VERIFICATION_ENABLED` (registration flow).

### Configuration Files
No `ACTIVITY_EVENTS` setting in `config/`, `deploy-templates/`, `docker/`, `docker-compose.yml`.

### Feature Flags and Deployment Concerns
`ACTIVITY_EVENTS_ENABLED` off -> all inserts silently skipped. Retention purge could remove old events (90 days default).

---

## 6. Risk Indicators

- Root cause not established from code alone: current `main` already writes the canonical real name at `personal_project_service.py:90`; the ticket's "0 events" is therefore either a stand-build/config difference, a name mismatch between what the harness queries and what is stored, or a swallowed insert failure. Needs the stand's logs (`[activity_events] async_insert failed`) or the harness's query value to confirm.
- Strongest code-backed lead: `EmailStr` lowercases the email domain for admin-created/registered users (verified), so stored email / project name / event `entity_id` are `...@email.com` while the harness uses `...@Email.com`; `find_all` equality is case-sensitive (`activity_repository.py:324`). This matches "control lowercase passes". Contradicts the ticket's premise that the project exists under the typed name — keeps `EmailStr` normalization and exact `entity_id` filtering in place (see Task Statements).
- Silent loss: `async_insert` swallows insert errors and the project commits anyway (`activity_repository.py:203-207`); an event lost this way is never retried because later logins hit the `_has_personal_project_complete` early return (line 76).
- Early-return path emits nothing (line 76-78): any flow that makes the application personal + membership before `ensure_personal_project_async` (none found in `src`) would leave no event.
- Speculative: fixes could touch (a) email normalization at DTO/storage, (b) event `entity_id` value, (c) event query matching; each affects different consumers listed under Consumers of Changed Contracts (budget customer id keyed by email, memberships, existing append-only event rows).
- Speculative: any change that alters stored project names touches existing memberships and the 16566 repair migration assumptions.
- `ACTIVITY_EVENTS_ENABLED` defaults False; retention purge deletes events after 90 days.
- Acceptance criterion 4 depends on a test in another repository; no way to run it from here.

---

## 7. Summary for Complexity Assessment

The change surface sits in the service layer (`personal_project_service.py`, one emitter at line 90) with possible adjacent touch points in the activity read path (`activity_repository.find_all`, line 324) and in the REST DTOs that feed the stored email (`UserCreateRequest`, `RegistrationRequest`, `rest_api/models/user_management.py`). Two repositories (`ApplicationRepository`, `ActivityEventRepository`) and the auth/user services that call `ensure_personal_project_async` (login, registration, per-request finalize, CLI analytics gate, migration coordinator) are consumers. No new patterns or dependencies are visible; the code already follows the shape the ticket asks for.

Technical novelty is low, but the diagnosis is open: the emitter already uses the canonical real name (EPMCDME-16566 is in this tree and unit-tested with mocks). The one concrete mismatch found is that `EmailStr` lowercases the email domain for admin-created and registered users (verified with the repo venv), so the stored email/project name/event `entity_id` can differ from the typed mixed-case string, and `entity_id` filtering is exact. Whether this is the stand's flow, or the stand runs an older build, or an insert failure is swallowed, is unverified.

Test posture: good mock-based unit coverage of the service, none end-to-end for the email-to-event-to-query path, and the acceptance test is in an external harness repo. Key risks: unclear root cause, silent best-effort event writes, shared name contracts (memberships, budget keyed by email, append-only event rows).

---

## 8. External References

None named by the task as a readable path. Referenced but outside this repository and not readable here: test-harness repo `tests/user/test_default_project_api.py::test_mixed_case_email_user_gets_one_membership_and_one_default_project` (xfail strict). Repo-internal related work read: `docs/superpowers/tasks/2026-10-29-epmcdme-16566-personal-project-case-insensitive/spec.md` (event `entity_id` uses canonical name; no email normalization, no data migration).

## Facts Added by Review

Researched for the names the spec relies on, after the analysis above was written. Read like the facts layer; where a line here contradicts one above, this one was read later.

- Column: `src/codemie/service/activity/activity_models.py:140-147` — `ActivityEvent.actor_id: Optional[str]`, `VARCHAR(36)`, `ForeignKey("users.id", ondelete="SET NULL")`, nullable. Migration column: `src/external/alembic/versions/38255069bfab_create_activity_events_table.py:30-35` (same type/FK).
- Input model: `activity_models.py:191` — `ActivityEventCreate.actor_id: Optional[str] = None`. Its validator (`:194-198`) only pairs `entity_type`/`entity_id`; `actor_id` is not validated.
- Result row: `activity_repository.py:47-48` — `ActivityEventRow.actor_id`, plus `actor_email`, `actor_name` joined from `UserDB`.
- API item: `src/codemie/rest_api/models/activity_event.py:29-31` — `ActivityEventListItem.actor_id`, `actor_email`, `actor_name`.
- Sentinel: `activity_repository.py:37` — `SYSTEM_ACTOR_ID = "system"`.
- Indexes: `ix_activity_events_actor_id_created_at` on `(actor_id, created_at)` partial `WHERE actor_id IS NOT NULL` (`activity_models.py:167-172`; migration `:55-60`).
- Write: `personal_project_service.py:91` sets `actor_id=user_id` (the user's UUID, not the email or project name). The event is inserted with `async_insert`, which copies it via `ActivityEvent(**event.model_dump())` (`activity_repository.py:195`) in a nested transaction (`session.begin_nested()`, `:194`) inside the isolated session. A failure of the event row (including the `users.id` FK) is logged `[activity_events] async_insert failed ...` and swallowed, returning `None` (`:203-207`); the personal-project transaction still commits (`personal_project_service.py:98`). With `ACTIVITY_EVENTS_ENABLED` false it returns `None` without inserting (`:189-190`).
- Readers: `activity_repository.py` (`find_all`, `find_by_actor` at `:254-269` and abstract `:91-100`), `activity_event_service.py:30,44,64`, `activity_events_router.py:55,70`. `find_by_actor` has no caller in `src` (grep of `find_by_entity|find_by_actor` outside `activity_repository.py` returns nothing; only `tests/codemie/service/activity/test_activity_repository.py:225-243`).
- Router: `activity_events_router.py:55` — `actor_id: list[str] | None = Query(None)`, repeated query param; passed unchanged to the service (`:70`).
- ORM: `src/codemie/service/activity/activity_models.py:160-166` — `Index("ix_activity_events_entity_type_entity_id", "entity_type", "entity_id", postgresql_where=text("entity_id IS NOT NULL"))` in `ActivityEvent.__table_args__`.
- Migration: `src/external/alembic/versions/38255069bfab_create_activity_events_table.py:49-54` (`op.create_index(op.f(...), "activity_events", ["entity_type","entity_id"], postgresql_where=sa.text("entity_id IS NOT NULL"))`); dropped in `downgrade()` at `:71`. Plain btree on the raw columns; no `lower(...)` expression. `entity_id` column is `Text` (`activity_models.py:138`, migration `:29`), `entity_type` is `VARCHAR(64)`.
- Historical design records mention it: `docs/superpowers/specs/2026-07-15-activity-events-design.md:87`, `docs/superpowers/tasks/2026-07-15-epmcdme-13275-scd-user-activity-tables/plan.md:205,629,650`. The task's own `spec.md:40` also names it (it states the index stays and is not used for the `entity_id` predicate; that is a statement in the spec, not code).
- Query shape in `find_all`: `entity_id` filter is `ActivityEvent.entity_id == entity_id` (`activity_repository.py:323-324`), exact equality on the raw column; `entity_type` filter is `entity_type.in_(list)` (`:321-322`). The other conditions are `event_type IN`, `domain IN`, `actor_id`, `created_at` ranges. The failing query in the ticket sends `event_type` + `entity_id` only, with no `entity_type`, so the leading index column `entity_type` is not constrained by the request (the router does not require `entity_type`: `activity_events_router.py:59-60`).
- Existing sibling indexes: `ix_activity_events_actor_id_created_at` and `ix_activity_events_domain_created_at` (`activity_models.py:167-177`).
- `find_by_entity` (`activity_repository.py:235-252`) uses `entity_type == ` and `entity_id ==` (both exact) and is the query shape this index matches by construction; it has no caller in `src`.
- `tests/codemie/service/project/test_personal_project_service.py:1242-1258, 1304-1343` — emitter tests with the repository mocked; assert `event.entity_id == self.REAL` (`:1258`, `:1343`) and no event when project and membership already exist (`:1317`).
- Emitter already writes the application's real stored name as `entity_id` (`personal_project_service.py:85-95`, `project_name` returned by `_create_personal_project`, `:208-291`); introduced by commit `7cd24080e1` (EPMCDME-16566), which changed `entity_id=user_email` to `entity_id=project_name`.
- Event insert is non-blocking (`async_insert` swallows event-row failures, `activity_repository.py:203-207`); the strict variant `async_insert_strict` (`:209-215`) propagates. `personal_project_service.py` uses the non-strict one.
- Read-side `entity_id` match is exact equality (`activity_repository.py:324`); `entity_type` and `event_type` are `IN` lists.
- "event should carry the canonical mixed-case personal-project name as entity_id": already true at `personal_project_service.py:90` (unit-tested at `test_personal_project_service.py:1242`); not verified against a running stand.
- "querying ... entity_id=<email> returns 0 results": consistent with exact equality at `activity_repository.py:324` when the queried string differs in case from the stored name; stand behaviour unverified.
- `find_all` `entity_id` parameter: called only by `activity_event_service.list_events` (`activity_event_service.py:43-56`), which is called only by `list_activity_events` (`activity_events_router.py:69`). Abstract signature at `activity_repository.py:120-134` carries `entity_id: str | None`. Tests calling it: `test_activity_repository.py:288,305,318,339,363`.
- `find_by_entity`: no caller in `src`; test at `test_activity_repository.py:225-234`.
