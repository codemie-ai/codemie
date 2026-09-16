# Technical Research

**Task**: project deletion integrations service repository
**Generated**: 2026-09-14
**Research path**: filesystem (codegraph unavailable)

---

## 1. Original Context

EPMCDME-14866 Project deletion is blocked by a non-zero integrations count while the integrations list is empty. Algorithm goes kinda like this - if there are something more than integrations - do not allow deletion, if there are integrations - delete integrations from DB first and then delete project. If there are no integrations - delete project sthraight away. My guess this is only API side work, no FE involved?

---

## 2. Codebase Findings

### Existing Implementations

- `src/codemie/rest_api/routers/projects.py` — `DELETE /v1/projects/{projectName}` endpoint (line 1139-1165), calls `project_service.delete_project`; `ProjectCounters` model (line 55-62) includes `integrations_count`.
- `src/codemie/service/project/project_service.py` — `ProjectService.delete_project` (line 452-517) and `_check_has_no_resources` (line 432-449), the guard blocking deletion when any resource count > 0.
- `src/codemie/repository/application_repository.py` — `get_project_entity_counts_bulk` (line 590-702), single UNION-ALL query producing all counters incl. `integrations_count`; `integrations_q` (line 664-672) is the root-cause query.
- `src/codemie/rest_api/models/settings.py` — `Settings` SQLModel table; `SettingType.USER` vs `SettingType.PROJECT`; `get_by_project_names` filters `setting_type == PROJECT`; `delete_setting`.
- `src/codemie/rest_api/routers/project_settings.py` — `GET/POST/PUT/DELETE /v1/settings/project*`, the actual "project integrations" CRUD surface; list (line 63-81) filters `settings_type=SettingType.PROJECT`.
- `src/codemie/rest_api/routers/user_settings.py` — `create_user_setting`/`create_setting` (~line 120-160) still stamps `request.project_name` onto USER-type rows.
- `src/codemie/service/settings/settings.py` — `SettingsService.create_setting` (line 379-459, sets `project_name` regardless of `settings_type`), `SettingsService.delete_setting` (line 573-602, handles per-credential-type OAuth token revocation).
- `src/codemie/service/settings/settings_index_service.py` — `_query_postgres` (line 53-86), backs the "integrations list" the ticket says is empty; filters `Settings.setting_type == settings_type`.
- `src/codemie/service/project/project_visibility_service.py` — `list_visible_projects_paginated` (line 60-124) also calls `get_project_entity_counts_bulk`, so the inflated count surfaces on project list/cards too.
- `src/codemie/repository/budget_repository.py` (line 170-180), `src/codemie/repository/project_budget_repository.py` (line 914-929) — `clear_project_on_deleted_budgets`/`clear_project_on_deleted_groups`, existing precedent for "detach soft-deleted rows before hard delete, still block on active ones."
- `src/codemie/rest_api/models/base.py` — `BaseModelWithSQLSupport.delete()` (line 537-541): opens its **own** `Session` and commits independently — does not participate in a caller-supplied session/transaction.

### Architecture and Layers Affected

API router (`projects.py`) → service (`ProjectService`) → repository (`application_repository`). Integrations live in a separate vertical: router (`project_settings.py`/`user_settings.py`) → service (`SettingsService`, `SettingsIndexService`) → model (`Settings`, no dedicated repository file). This task cross-links the two verticals for the first time.

### Integration Points

`project_service.py` currently does **not** import anything from `codemie.service.settings` or `codemie.rest_api.models.settings` — no existing cross-link between project deletion and settings/integrations cleanup. Wiring the fix requires a new dependency from `ProjectService` (or a new call site) into `Settings`/`SettingsService`.

### Patterns and Conventions

- Bulk entity-count via one `UNION ALL` query (`get_project_entity_counts_bulk`), shared by both the deletion guard and the list/counters response — same root cause feeds both symptoms.
- "Detach, don't delete" pattern for budgets/groups: soft-deleted rows get `project_name` nulled before project deletion; active ones still block. `Settings` has **no** soft-delete column, so integrations must be hard-deleted, not detached.
- `project_name` columns on `Assistant`, `WorkflowConfig`, `Skill`, `IndexInfo`, `Settings` are plain indexed strings, not FK constraints — no DB-level cascade delete exists.
- `SettingType` (`USER` vs `PROJECT`) is the discriminator for "genuine project-scoped integration" vs. "personal credential that merely carries a project_name." Every real integrations list/read path filters on `setting_type == PROJECT`; `get_project_entity_counts_bulk`'s `integrations_q` does not.

---

## 3. Documentation Findings

### Guides and Architecture Docs

`.ai-run/guides/architecture/service-layer-patterns.md` and `.ai-run/guides/data/repository-patterns.md` are generic, no integrations/project-deletion-specific guidance. `.ai-run/guides/integration/*` guides cover external tool integrations (Jira/Confluence/Xray/GDocs/MCP) — unrelated to this project-settings "integrations" feature.

### Architectural Decisions

None recorded specific to this bug. Closest precedent: budget/group detach-then-delete flow inside `ProjectService.delete_project`.

### Derived Conventions

Resource-count blocking logic lives centrally in `ProjectService._check_has_no_resources`. Any entity type meant to auto-cleanup instead of block must be (a) removed from the blocking check and (b) handled explicitly before `application_repository.delete_by_name` is called, following the existing `affected_budget_ids = ...; application_repository.delete_by_name(...)` ordering in `delete_project`.

---

## 4. Testing Landscape

### Existing Coverage

- `tests/codemie/service/project/test_project_service_delete_update.py` — full unit coverage of `delete_project`. `test_project_with_integrations_raises_409` (line 227-243) currently asserts integrations block deletion with 409 — this assertion inverts under the fix. `test_budgets_and_groups_are_detached_before_delete` (line 420-438) shows the existing detach-then-delete test pattern.
- `tests/codemie/repository/test_application_repository_entity_counts.py` — `test_integrations_counted_correctly` (line 123-130) only checks the raw UNION result is mapped, no assertion on `setting_type` filtering (confirms the gap).
- `tests/codemie/rest_api/routers/test_projects_router.py` (~line 1336-1450) — router-level delete tests, success/403/409 propagation via mocked `project_service`.
- `tests/codemie/rest_api/routers/test_project_settings.py` — confirms `settings_type=SettingType.PROJECT` filtering at router layer.

### Testing Framework and Patterns

pytest; heavy `unittest.mock.patch`/`MagicMock` for service/repo isolation; SQL assertions via compiling SQLAlchemy statements to a Postgres dialect string and substring-checking (`_compile_sql` helper). Class-per-scenario grouping (`TestGetProjectEntityCountsBulk`, `TestProjectServiceDeleteProject`); `_zero_counts`/`_counts_with` builder helpers; `@patch` on module-level singletons imported into `project_service` (`application_repository`, `budget_repository`, `user_project_repository`, `activity_event_repository`).

### Coverage Gaps

- No test verifies `integrations_q` filters by `setting_type == PROJECT`.
- No test exists for "auto-delete integrations then delete project" — new code path.
- No integration/e2e test covers `DELETE /v1/projects/{projectName}` against a real/test DB with mixed `setting_type` rows.

---

## 5. Configuration and Environment

### Environment Variables

None scoped to integrations counting or project deletion.

### Configuration Files

None specific beyond standard `config/`/`.env.example`.

### Feature Flags and Deployment Concerns

`customer_config.is_feature_enabled(PERSONAL_LITELLM_FEATURE)` gates whether non-admins can create personal LiteLLM settings (`user_settings.py`) — relevant context since personal (USER-type) LiteLLM settings are exactly the kind of row that can carry a stray `project_name` and inflate the count. No Dockerfile/CI changes implied.

---

## 6. Risk Indicators

- **Root cause confirmed**: `get_project_entity_counts_bulk`'s `integrations_q` (`application_repository.py:664-672`) counts every `Settings` row matching `project_name` with no `setting_type` filter, while every real "integrations list" surface filters `setting_type == PROJECT`. `SettingsService.create_setting` stamps `project_name` onto USER-type rows too, so a project can show `integrations_count > 0` while its actual integrations list is empty.
- Same shared method feeds `ProjectCounters.integrations_count` on project list/cards (`project_visibility_service.py:99`) — the inflated count is user-visible there too, not just on the delete path.
- `Settings.delete()`/`delete_setting()` open and commit their **own** session, unlike `ProjectService.delete_project` which takes a caller-supplied `session` committed once by the router. Wiring "delete integrations then delete project" atomically requires either reusing the passed-in session for integration deletes (bypassing `Settings.delete()`) or accepting non-atomic cleanup.
- Decision needed: bulk SQL delete of `Settings` rows vs. reusing `SettingsService.delete_setting`'s per-credential-type cleanup (OAuth token revocation for Google/GitLab/Jira/Confluence, `settings.py:573-602`). Ticket says "delete integrations from DB first" — favors the service-level path so OAuth tokens get revoked, not a raw bulk delete that would orphan external tokens.
- `test_project_with_integrations_raises_409` will need to change from asserting a block to asserting auto-delete-then-success.
- User's own read of scope ("API side work, no FE involved") is consistent with findings — no FE code found in this research; response shape (`ProjectCounters`) is unchanged, only counting/deletion logic shifts.

---

## 7. Summary for Complexity Assessment

The task touches two layers: the repository counting query (`application_repository.get_project_entity_counts_bulk`) and the service-level deletion flow (`ProjectService.delete_project`/`_check_has_no_resources`), with a new cross-vertical dependency from `project` service code into `settings` service/model code that does not currently exist. The fix has two parts: (1) align `integrations_q` with the real integrations list by filtering `setting_type == PROJECT` (fixes the "count non-zero, list empty" symptom), and (2) change the blocking semantics for integrations specifically — remove it from the hard block in `_check_has_no_resources` and instead auto-delete matching `Settings` rows before calling `application_repository.delete_by_name`, mirroring the existing budget/group detach-then-delete ordering already in `delete_project`.

Technical novelty is moderate: the counting-query fix is a one-line filter change with a direct existing test to update, but the auto-delete-integrations path is genuinely new — no prior code deletes `Settings` rows as part of project deletion, and the session-ownership mismatch (`Settings.delete()` opens its own session vs. `delete_project`'s caller-supplied session) needs an explicit decision to keep the operation reasonably atomic. Test coverage is solid at the unit level for `delete_project` and the counts query but has zero coverage for the new auto-delete-integrations behavior and zero e2e coverage of the full delete flow. Risk is concentrated in transaction/atomicity boundaries and in whether integration deletion should route through `SettingsService.delete_setting` (preserves OAuth token revocation) versus a bulk SQL delete (simpler but risks orphaned external credentials). No FE surface was found for this feature — API-only, consistent with the reporter's own assessment.
