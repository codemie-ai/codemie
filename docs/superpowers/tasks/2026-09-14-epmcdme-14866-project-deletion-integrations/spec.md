# Spec — EPMCDME-14866: Fix project deletion blocked by inflated integrations count

## Problem

`DELETE /v1/projects/{projectName}` is blocked with 409 by a non-zero `integrations_count` even when the project's real integrations list is empty.

**Root cause**: `application_repository.get_project_entity_counts_bulk`'s `integrations_q` counts every `Settings` row matching `project_name`, with no `setting_type` filter. Every real "integrations list" surface (`Settings.get_by_project_names`, `SettingsIndexService`, the `GET /v1/settings/project` router) filters to `setting_type == PROJECT` only. `SettingsService.create_setting` stamps `project_name` onto `USER`-type rows too (e.g. a personal LiteLLM/GitLab credential), inflating the count with rows that are not real project integrations.

## Desired behavior (per ticket)

- If a project has other blocking resources (assistants, workflows, datasources, skills, active budgets/budget groups) — deletion is still blocked with 409, unchanged.
- If a project has integrations (real, `setting_type == PROJECT` rows) — do not block; delete the integrations from the DB first, then delete the project.
- If a project has no integrations — delete the project straight away (unchanged fast path).

API-only change. No FE surface found for this feature; response shapes (`ProjectCounters`) are unchanged.

## Design

1. **`src/codemie/repository/application_repository.py:664-672`** — `integrations_q` in `get_project_entity_counts_bulk` gets `.where(Settings.setting_type == SettingType.PROJECT.value)` added, aligning the count with every real integrations-list surface.
2. **`src/codemie/service/project/project_service.py:432-449`** — `_check_has_no_resources` excludes `integrations_count` from the blocking `sum(counts.values()) > 0` check. Still blocks on assistants/workflows/datasources/skills/budgets/budget_groups. Returns the `counts` dict to its caller (was `None`).
3. **`src/codemie/service/project/project_service.py:451-517`** — `delete_project`, after the resource check passes: if `counts.get("integrations_count", 0) > 0`, fetch `Settings.get_by_project_names([project_name])` (already `setting_type == PROJECT` filtered) and call `SettingsService.delete_setting(setting.id)` per returned row — before the existing budget/group detach and `application_repository.delete_by_name` call. `SettingsService` is already imported in this file (line 45); reusing `delete_setting` preserves existing per-credential-type OAuth token revocation (Google/GitLab/Jira/Confluence). Activity event attributes gain `affected_integrations`, mirroring the existing `affected_budgets`/`affected_budget_groups` fields.

**Order in `delete_project`**: personal-project check → assigned-users check → resource check (blocking, integrations excluded) → delete integrations (if any) → detach budgets/groups → delete project row → activity log.

**Atomicity**: non-atomic by design (user-approved trade-off). `Settings.delete()` opens its own session; `delete_project` uses a caller-supplied session. Integrations are deleted first, so a subsequent project-delete failure just leaves 0 integrations behind — safe to retry, no orphaned project, no session-sharing refactor needed.

## Testing

- `tests/codemie/repository/test_application_repository_entity_counts.py` — extend/add a test asserting `integrations_q`'s compiled SQL filters on `setting_type == PROJECT`, following the existing `_compile_sql` substring-check pattern.
- `tests/codemie/service/project/test_project_service_delete_update.py` — rewrite `test_project_with_integrations_raises_409` (currently asserts 409) to assert `delete_project` now succeeds when `integrations_count > 0`: mock `SettingsService.delete_setting` and `Settings.get_by_project_names`, assert `delete_setting` called once per returned setting id, and `application_repository.delete_by_name` is still called. Keep the other `_check_has_no_resources` 409 tests (assistants/workflows/datasources/budgets/budget_groups) passing unchanged.
- New test: zero-integrations path still calls `delete_by_name` directly with no `SettingsService` calls ("if there are no integrations - delete project straight away").

## Out of scope

- No session/transaction refactor for true atomicity (explicitly declined).
- No FE changes.
- No change to `SettingsService.delete_setting`'s per-credential-type cleanup logic itself.
