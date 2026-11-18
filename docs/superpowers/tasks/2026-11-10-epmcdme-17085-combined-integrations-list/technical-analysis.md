# Technical Research

**Task**: settings integrations combined-list pagination visibility
**Generated**: 2026-11-10
**Research path**: filesystem
**Workspace**: root `/Users/Vadym_Vlasenko/AI/projects`; repo `codemie` (`codemie/`, branch `EPMCDME-17084_unified-integrations-list`, rev `8b08abb93`)

---

## 1. Original Context

Story `story.md` in this run dir (EPMCDME-17085, child of EPMCDME-17084), verbatim below the frontmatter:

> # Combined Integrations List (backend) — Story
> **Date**: 2026-11-10 · **Status**: Approved · **Ticket**: EPMCDME-17085 · **Parent**: ../../story.md
>
> ## Context
> - Integrations are listed through two paged settings lists, one per scope. Each applies its own visibility rules: the User list returns only the person's own integrations and hides system-created internal settings; the Project list returns only projects the person administers (every project for platform admins) and hides project-budget LiteLLM integrations from anyone but platform admins and maintainers. Both are sorted newest first and return a page plus a total.
> - No list returns both scopes together, and neither accepts a scope filter. Filters accepted today are search, project, created by, type and Global; unknown filter keys are silently ignored.
> - The "available settings" list used by assistant and data source pickers returns both scopes, but without paging or filters and with member (not admin) project visibility, so it does not fit a page list.
> - Each stored integration already records its scope, and permission checks for creating, changing and deleting Project integrations stay per project.
>
> ## Complexity
> **Size**: M (16/36) · **Recommended flow**: sdlc-standard
> One paged, newest-first query across both scopes that reuses the existing per-scope visibility branches; API, service and a read-only query in about 4–6 files, no schema change.
>
> ## Story
> **As a** CodeMie user opening the Integrations page, **I want** one list that returns my User and Project integrations together, filtered by scope, sorted and paged **so that** the page can show both scopes in one table with correct page numbers and totals.
>
> ## Background
> The unified Integrations page (parent story) replaces the User/Project tabs with one table and a Scope filter. Paging and newest-first sorting across both scopes cannot be done correctly from two separately paged lists, so the page needs one list that serves both scopes while keeping exactly today's rules about who sees what.
>
> ## Acceptance Criteria
> - [ ] Given a person requests the combined list with Scope All, then integrations of both scopes come back as one list sorted newest first, paged, with a total that counts both scopes.
> - [ ] Given the combined list with Scope All, then for each scope it holds exactly the rows today's per-scope lists return to that person, with their exclusions (system-created internal settings; project-budget LiteLLM integrations for anyone but platform admins and maintainers).
> - [ ] Given a request with Scope User or Scope Project, then the result has the same rows, order and total as today's per-scope list for that person.
> - [ ] Given no scope is sent, then the list behaves as Scope All.
> - [ ] Given the filters the page offers today (search, project, type, Global, Created by) are sent together with a scope, then they narrow the result the same way they narrow today's per-scope lists.
> - [ ] Given every row in the result, then it states its scope (User or Project) and, for User rows, whether it is Global.
> - [ ] Given a member who administers no project requests Scope Project, then the result is empty and contains no other person's or project's integrations.
> - [ ] Given a project admin requests Scope All, then project-budget LiteLLM integrations of the projects they administer are not included.
> - [ ] Given an unknown scope value, then the request is rejected with a validation error naming the allowed values.
> - [ ] Given the admin project page keeps using today's per-scope lists, then their results are unchanged by this story.
>
> ## Out of Scope
> - Any page, filter or form change (child `codemie-ui-unified-list`).
> - Changes to creating, updating or deleting integrations, and to their permission checks.
> - The Schedulers list (separate follow-up story).
>
> ## Decisions
> - D2: How is "All" scopes served? → A new combined list in `codemie`: both scopes in one date-sorted, paged result with a scope filter — user (parent D2)
> - D11: Does the combined list restate who sees what? → No: it applies the visibility rules of the existing settings lists, with scope as an optional filter (All by default); the per-scope lists stay and return the same rows as Scope User and Scope Project — proposed by the architecture review; user approved the parent story with it (parent D11)
>
> ## Open Questions
> - None
>
> ## Constraints
> - Lands first: `codemie-ui-unified-list` reads the Integrations page from this list (producer → consumer).
> - Contract produced for `codemie-ui`: an optional scope filter (All, User, Project), today's filters, paging and total, newest first, and per-row scope and Global flag.
> - The per-scope visibility rules keep one home; the combined list reuses them rather than restating them (D11).

---

## 2. Codebase Findings

**Repository impact**: `codemie` — affected (evidence below). `codemie-ui` — not selected; consumer claims about it are unverified.

### Existing Implementations

**Purpose A — a person pages through the integrations of one scope they may see, newest first.**
- `SettingsIndexService.run` / `_query_postgres` — `codemie:src/codemie/service/settings/settings_index_service.py:31-93`. Consumers: `GET /v1/settings/user` (`routers/user_settings.py:110-128`), `GET /v1/settings/project` (`routers/project_settings.py:68-86`). Rules, all inside one private classmethod:
  - `setting_type == settings_type` (:65) — rows with NULL `setting_type` appear in neither list (column nullable, `src/external/alembic/versions/074d06e75b25_create_settings.py:102`).
  - PROJECT: unless `user.is_admin`, `project_name IN admin_project_names` (:66-68). `is_admin` is forced true for maintainers (`rest_api/security/user.py:125-126`) and for everyone when `ENV=local` (:118-119).
  - PROJECT: unless `is_admin_or_maintainer`, excludes `credential_type == LITE_LLM AND alias LIKE 'codemie:project:%'` (:69-75).
  - Any other value (USER): `user_id == user.id` and `alias NOT LIKE '__internal__%'` (:76-78); `_` is a LIKE wildcard there, unescaped.
  - `SettingsFilter.add_sql_filters` (:80-81); `ORDER BY date DESC` only, no tiebreaker (:84); `date` is nullable (alembic `074d06e75b25:60`, index `ix_settings_date` `models/settings.py:280`).
  - Total via `count(*)` over the filtered subquery (:87); `OFFSET page*per_page LIMIT per_page` (:88); sensitive values masked (:44-47); envelope `{data, pagination:{page, per_page, total, pages: ceil(total/per_page)}}` (:49-51) — `per_page=0` divides by zero.
  - Routers: no `response_model`; `filters` is a JSON string parsed with `json.loads` (`user_settings.py:124`, `project_settings.py:82`); `page`/`per_page` are unvalidated ints. Rows serialise as `Settings` (fields include `id`, `date`, `update_date`, `setting_type`, `is_global`, `created_by`).
- `SettingsFilter` — `service/filter/filter_services.py:94-114`: `search` (ILIKE over `project_name`, `alias`), `project` (= / IN), `created_by` (ILIKE `created_by.name`), `type` (credential_type = / IN), `is_global` (bool =). No scope key. Unknown keys skipped silently (`base_filter_service.py:79-81`). Only consumer: `settings_index_service.py:81`.

**Purpose B — one list with both scopes.**
- `GET /v1/settings/user/available` — `routers/user_settings.py:131-158`. Both scopes, unpaged, unfiltered, user rows then project rows, no sort. Project visibility: admin/maintainer all (:147-148); applications admin only `admin_project_names` (:149-152); otherwise member `project_names` (:153-156). Budget rows dropped in Python (:157); user rows via `SettingsService.get_settings` with `startswith('__internal__')` (`service/settings/settings.py:280`). A caller-supplied `scope` query param is pinned as ignored (`tests/.../routers/test_user_settings.py:106-121`). Other consumers: `codemie-ui` pickers (unverified).
- None found that pages or sorts across both scopes (searched `setting_type`, `SettingType.`, `SettingsIndexService`, `/settings` routes).

**Purpose C — keep project-budget LiteLLM keys from non-admins.**
- `SettingsService.is_budget_integration_hidden_from` — `service/settings/settings.py:305-313` (Python predicate, per row). Consumers: `user_settings.py:157`, `project_settings.py:159, 218`, `service/iac_export/exporters/integration.py:62, 77`. The same rule is restated as SQL in `settings_index_service.py:69-75`: two homes today.

**Purpose D — "Created by" options.**
- `SettingsIndexService.get_users` — `settings_index_service.py:95-132`: PROJECT limited to admin projects unless `is_admin`, no budget exclusion; USER own rows; `settings_type=None` applies no type and no visibility filter. Only consumer: `GET /v1/settings/project/users` (`project_settings.py:53-65`) with PROJECT.

**Other touched code**
- `SettingType` enum `USER="user"`, `PROJECT="project"` (`rest_api/models/settings.py:252-254`); `setting_type` default USER, `is_global` default False (:268-269).
- `routers/settings.py:15` — module documented as "Settings endpoints that span both the user and project scopes" (only `POST /v1/settings/transfer` today); routers registered `rest_api/main.py:79-81`.

### Decisions Already in the Code
- Per-scope visibility is an inline `if/else` in `_query_postgres` (:65-78), not separable query pieces; alternative in the codebase: composable `QueryModifier` classes (`service/workflow_config/workflow_config_index_service.py:40-98`). Reason: none found.
- Project listing visibility = administered projects (:66-68), while the pickers list uses member projects (`user_settings.py:153-156`). Reason: none found.
- Visibility keyed on `is_admin` for projects and `is_admin_or_maintainer` for budget keys; equivalent because maintainers get `is_admin=True` (`security/user.py:125-126`).
- Convention: list filters travel as one JSON `filters` string (`user_settings.py:117`, `project_settings.py:75`); scope selectors elsewhere are enum-typed query params (`routers/assistant/catalog.py:91` `AssistantScope`, `routers/workflow.py:250` `WorkflowScope | None`). Request validation errors become 422 with `"<loc>: <pydantic msg>"` (`rest_api/main.py:1761-1810`).
- Convention: list routes return the raw dict, no `response_model`; guide prefers explicit response models for user-facing shapes (`.ai-run/guides/api/endpoint-conventions.md`).
- `setting_type` is rewritten on every update from the endpoint used (`service/settings/settings.py:525-528`); `is_global` is stored from the request for both scopes (:447, :531).
- The OpenAPI contract is snapshot-pinned: `tests/codemie/rest_api/router_guards/test_openapi_snapshot.py` against `baselines/openapi.json` (settings paths at lines 74827-75319), regenerated manually via `python -m tests.codemie.rest_api.router_guards.regenerate_baselines`; route order pinned by `baselines/route_precedence.json`.

### Task Statements Checked Against the Code
- User list: own rows only, internal settings hidden — **holds** (`settings_index_service.py:76-78`).
- Project list: administered projects, every project for platform admins — **holds** (:66-68); maintainers also see every project (`security/user.py:125-126`).
- Budget LiteLLM hidden from all but admins/maintainers — **holds** (:69-75).
- Both newest first with page and total — **holds** (:84, :49-51); order among equal or NULL `date` is not deterministic today.
- No list returns both scopes paged; no scope filter — **holds** (`filter_services.py:94-114`).
- Filters search/project/created by/type/Global; unknown keys ignored — **holds** (`filter_services.py:94-114`, `base_filter_service.py:79-81`).
- "Available" list has member (not admin) project visibility — **contradicted in part**: applications admins get admin projects only, admins/maintainers all (`user_settings.py:147-152`).
- Each stored integration records its scope — **holds**, column nullable (alembic `074d06e75b25:102`).
- Project create/change/delete permissions per project — **holds** (`project_settings.py:291-302`, :152-160, :216-219).
- D11 / "per-scope rules keep one home" — **keeps `_query_postgres` per-scope branches in place**; consumers `GET /v1/settings/user`, `GET /v1/settings/project`. The budget rule already has two homes (Purpose C).
- Unknown scope rejected naming allowed values — **holds for enum query params** (`main.py:1791-1810`); a key inside the `filters` JSON would be ignored instead (`base_filter_service.py:79-81`). `SettingType` has no "all" member.
- Admin project page keeps per-scope lists — **unverified** (`codemie-ui` not selected).
- "No schema change" — fields the story names (`setting_type`, `is_global`, `date`) exist (`models/settings.py:257-280`).

### Consumers of Changed Contracts
- Branch diff: none (HEAD `8b08abb93` equals merge-base with `origin/main`; working tree changes only `.gitignore` and this run dir).
- `SettingsIndexService.run` / `_query_postgres`: `user_settings.py:126`, `project_settings.py:84`; tests `tests/codemie/service/settings/test_settings_index_service.py:35-81`; router tests mock it (`test_user_settings.py:74`, `test_project_settings.py:55`, `test_user_settings_crud.py:119-184`). `BaseSettingsService._decrypt_fields` via this class: `service/index/index_encrypted_settings_service.py:97`.
- `SettingsFilter`: only `settings_index_service.py:81`.
- `SettingsIndexService.get_users`: only `project_settings.py:64`.
- OpenAPI/route baselines: any added route changes them.

### Architecture and Layers Affected
- API: `routers/user_settings.py`, `routers/project_settings.py`, `routers/settings.py` (cross-scope module).
- Service: `SettingsIndexService`, `SettingsService` (visibility constants/predicate), `SettingsFilter`.
- Data: `Settings` SQLModel, PostgreSQL via `sqlmodel.Session(Settings.get_engine())`; read-only.

### Integration Points
- `codemie-ui` (producer → consumer per story Constraints; consumer side unverified).
- No external services.

### Patterns and Conventions
- Router validates and delegates; service owns query (`.ai-run/guides/architecture/service-layer-patterns.md`, endpoint-conventions "Router Size").
- Raw SQLModel `select` + `SettingsFilter.add_sql_filters` + count-subquery pagination (`settings_index_service.py:63-91`).

---

## 3. Documentation Findings

### Guides and Architecture Docs
- `.ai-run/guides/api/endpoint-conventions.md` (response models, router size); `.ai-run/guides/data/database-optimization.md` (paginate list endpoints); `.ai-run/guides/testing/testing-api-patterns.md`. None covers settings visibility.

### Architectural Decisions
- No ADR. Budget-key rule documented only in a docstring (`service/settings/settings.py:307`). Pickers-list visibility documented in docstring (`user_settings.py:138-146`). Parent story D2/D11 (Section 8).

### Derived Conventions
- Enum query param for scope; JSON `filters` string for list filters; `{data, pagination}` envelope.

---

## 4. Testing Landscape

### Existing Coverage
- `tests/codemie/service/settings/test_settings_index_service.py` — `run` for USER/PROJECT and `get_users`, with `Session` mocked: envelope and masking asserted, WHERE clauses not.
- `tests/codemie/rest_api/routers/test_user_settings.py`, `test_project_settings.py`, `test_user_settings_crud.py` — list routes with the service mocked; available-list visibility per role.
- `tests/codemie/rest_api/router_guards/` — OpenAPI and route-precedence snapshots.

### Testing Framework and Patterns
- pytest + anyio, `httpx.AsyncClient(ASGITransport(app))`, `@patch` of `LocalIdp.authenticate` and service classmethods; no real-DB fixture for settings queries.

### Coverage Gaps
- No test asserts the index query's visibility clauses: admin-project restriction, budget exclusion, `__internal__` exclusion (grep of these constants in the settings tests: none).
- No test of ordering ties, NULL `date`, or `per_page=0`.

---

## 5. Configuration and Environment

### Environment Variables
- None specific. `ENV=local` makes every user admin (`security/user.py:118-119`); `ENABLE_USER_MANAGEMENT`, `ADMIN_USER_ID`, `ADMIN_ROLE_NAME` decide `is_admin` (:120-123).

### Configuration Files
- None for the list.

### Feature Flags and Deployment Concerns
- `personalLiteLLMIntegrations` customer feature gates personal LiteLLM create/update only (`user_settings.py:63, 95-107`), not listing.

---

## 6. Risk Indicators

- The per-scope rules sit inline in a private method (`settings_index_service.py:65-78`); D11 keeps them as the single home — see *Task Statements*.
- The budget rule already has two homes (Python predicate `settings.py:305-313`, SQL `settings_index_service.py:69-75`); see *Existing Implementations*, Purpose C.
- AC "same rows, order and total": order among equal/NULL `date` is not deterministic in today's lists (`:84`).
- AC "unknown scope rejected": a scope carried inside `filters` JSON would be silently ignored (`base_filter_service.py:79-81`).
- `is_global` filter applies to any row with the column set; project rows store `is_global` from the request (`settings.py:447`).
- `get_users(None)` applies no visibility (`settings_index_service.py:112-121`).
- OpenAPI and route-precedence snapshots fail on any new route until regenerated.
- Visibility clauses have no query-level tests (Section 4).
- Speculative: a combined query whose scope branches differ from the per-scope ones would break parity ACs silently, given those gaps.

---

## 7. Summary for Complexity Assessment

Backend-only, read-only change in `codemie`. Layers: API (`user_settings.py` / `project_settings.py` / cross-scope `routers/settings.py`), service (`SettingsIndexService`, `SettingsFilter`, `SettingsService` constants), PostgreSQL read via SQLModel. Every field the story names already exists (`setting_type`, `is_global`, `date`); the branch carries no diff yet.

Not novel: today's two lists share one paged, date-sorted query whose per-scope visibility is an inline `if/else` (`settings_index_service.py:65-78`); D11 requires that to stay the one home. Precedents exist for enum scope params (assistants, workflows) and composable query modifiers (workflows). Validation errors already return 422 naming the field and pydantic message.

Tests: service tests mock `Session` and never assert visibility clauses; router tests mock the service; nothing covers ordering ties, NULL dates or `per_page=0`. OpenAPI/route snapshot guards will need regeneration. Key risks: parity with today's per-scope rows/order, the budget rule's two homes, and scope values sent inside the ignored-key `filters` JSON.

---

## 8. External References

- `codemie:docs/superpowers/tasks/2026-11-10-epmcdme-17085-combined-integrations-list/story.md` — resolved; identical to `codemie-ui:docs/stories/2026-11-10-integrations-unified-list/children/codemie-combined-list/story.md`. Pasted in Section 1.
- Parent `../../story.md` — does not resolve from this run dir; resolved at `codemie-ui:docs/stories/2026-11-10-integrations-unified-list/story.md`. Facts used: D2 "the UI reads the page from it"; D11 "the per-scope lists stay for the admin project page and return the same rows as Scope User and Scope Project"; AC "Global mark on global User integrations"; D7 "Created by only when Scope is Project … a hint when All"; D8 "Scope filter only for people who can see project integrations".
- Parent architecture review brief (cited by the previous analysis as `arch-review/arch-review-brief.local.md`) — unresolved: `arch-review/` is empty.
