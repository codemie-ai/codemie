# EPMCDME-14866: Project Deletion Integrations Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix project deletion so it is no longer blocked by an inflated integrations count, and instead auto-deletes real project integrations before deleting the project.

**Architecture:** Two changes in the existing delete path, no new files. (1) The bulk entity-count query gains a `setting_type == PROJECT` filter so `integrations_count` matches the real integrations list. (2) `ProjectService.delete_project` stops blocking on integrations and instead deletes them via `SettingsService.delete_setting` before the project row is removed.

**Tech Stack:** Python 3.12, FastAPI, SQLModel/SQLAlchemy, pytest, unittest.mock.

**Spec:** `docs/superpowers/tasks/2026-09-14-epmcdme-14866-project-deletion-integrations/spec.md`

## Global Constraints

- No FE changes — this is an API-only fix.
- No session/transaction refactor for full atomicity (explicitly out of scope per spec).
- `SettingsService.delete_setting` is the deletion path for integrations, not a raw bulk SQL delete (preserves OAuth token revocation).
- Commit messages use `EPMCDME-14866: <description>` per repo convention.

---

### Task 1: Filter integrations count query by setting_type == PROJECT

**Files:**
- Modify: `src/codemie/repository/application_repository.py:611` (import), `:664-672` (`integrations_q`)
- Test: `tests/codemie/repository/test_application_repository_entity_counts.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `get_project_entity_counts_bulk` unchanged signature `(session: Session, project_names: list[str]) -> dict[str, dict]`; behavior change only — `integrations_count` now reflects `setting_type == PROJECT` rows only.

Test-first: yes — new test asserting the compiled `integrations_q`/combined statement filters on `setting_type = 'project'`.

- [ ] **Step 1: Write the failing test**

Add to `TestGetProjectEntityCountsBulk` in `tests/codemie/repository/test_application_repository_entity_counts.py` (near `test_integrations_counted_correctly`, using the file's existing `_compile_sql` helper):

```python
    def test_integrations_query_filters_by_setting_type_project(self):
        """integrations_q only counts Settings rows with setting_type == PROJECT."""
        mock_session = MagicMock()
        mock_session.exec.return_value.all.return_value = []

        application_repository.get_project_entity_counts_bulk(mock_session, ["my-proj"])

        compiled_statement = mock_session.exec.call_args[0][0]
        sql = _compile_sql(compiled_statement)
        assert "setting_type" in sql
        assert "'project'" in sql
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/codemie/repository/test_application_repository_entity_counts.py::TestGetProjectEntityCountsBulk::test_integrations_query_filters_by_setting_type_project -v`
Expected: FAIL — `assert "setting_type" in sql` fails because the current `integrations_q` has no `setting_type` filter.

- [ ] **Step 3: Write minimal implementation**

In `src/codemie/repository/application_repository.py`, update the lazy import at line 611 to also bring in `SettingType`:

```python
        from codemie.rest_api.models.settings import Settings, SettingType
```

Update `integrations_q` (lines 664-672):

```python
        integrations_q = (
            select(
                Settings.project_name.label("proj"),
                literal("integrations").label("entity_type"),
                func.count(Settings.id).label("cnt"),
            )
            .where(
                Settings.project_name.in_(project_names),
                Settings.setting_type == SettingType.PROJECT.value,
            )
            .group_by(Settings.project_name)
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/codemie/repository/test_application_repository_entity_counts.py -v`
Expected: PASS — all tests in the file, including the new one and the pre-existing `test_integrations_counted_correctly`.

- [ ] **Step 5: Commit**

```bash
git add src/codemie/repository/application_repository.py tests/codemie/repository/test_application_repository_entity_counts.py
git commit -m "EPMCDME-14866: Filter integrations count by setting_type=PROJECT"
```

---

### Task 2: Stop blocking deletion on integrations; auto-delete them first

**Files:**
- Modify: `src/codemie/service/project/project_service.py:432-449` (`_check_has_no_resources`), `:451-517` (`delete_project`)
- Test: `tests/codemie/service/project/test_project_service_delete_update.py`

**Interfaces:**
- Consumes: `Settings.get_by_project_names(project_names: list[str]) -> list[Settings]` (from `codemie.rest_api.models.settings`, already filters `setting_type == PROJECT`); `SettingsService.delete_setting(credential_id: str, user_id: Optional[str] = None) -> None` (from `codemie.service.settings.settings`, already imported in this file at line 45); `application_repository.get_project_entity_counts_bulk` (unchanged, from Task 1).
- Produces: `_check_has_no_resources(session, project_name, action) -> dict` — now returns the `counts` dict for the project (was `-> None`); still raises 409 if any *non-integrations* count is > 0. `delete_project` behavior: integrations no longer block deletion; they are deleted via `SettingsService.delete_setting` before the project row is removed.

Test-first: yes — rewrite `test_project_with_integrations_raises_409` to assert success + `delete_setting` calls, add a zero-integrations passthrough test.

- [ ] **Step 1: Write the failing tests**

In `tests/codemie/service/project/test_project_service_delete_update.py`, replace the existing `test_project_with_integrations_raises_409` test (currently lines 225-243) with:

```python
    @patch("codemie.service.project.project_service.Settings")
    @patch("codemie.service.project.project_service.SettingsService")
    @patch("codemie.service.project.project_service.user_project_repository")
    @patch("codemie.service.project.project_service.application_repository")
    def test_project_with_integrations_deletes_them_then_succeeds(
        self, mock_app_repo, mock_upr, mock_settings_service, mock_settings
    ):
        """delete_project auto-deletes project integrations instead of blocking on them."""
        mock_session = MagicMock()
        mock_upr.get_by_project_name.return_value = []
        mock_app_repo.get_project_entity_counts_bulk.return_value = _counts_with("my-project", integrations_count=2)
        setting_1 = MagicMock(id="setting-1")
        setting_2 = MagicMock(id="setting-2")
        mock_settings.get_by_project_names.return_value = [setting_1, setting_2]

        ProjectService.delete_project(
            session=mock_session,
            project_name="my-project",
            project_type=Application.ProjectType.SHARED,
            actor_id="user-1",
            action="DELETE /v1/projects/my-project",
        )

        mock_settings.get_by_project_names.assert_called_once_with(["my-project"])
        assert mock_settings_service.delete_setting.call_count == 2
        mock_settings_service.delete_setting.assert_any_call("setting-1")
        mock_settings_service.delete_setting.assert_any_call("setting-2")
        mock_app_repo.delete_by_name.assert_called_once_with(mock_session, "my-project")

    @patch("codemie.service.project.project_service.Settings")
    @patch("codemie.service.project.project_service.SettingsService")
    @patch("codemie.service.project.project_service.user_project_repository")
    @patch("codemie.service.project.project_service.application_repository")
    def test_project_with_zero_integrations_skips_settings_lookup(
        self, mock_app_repo, mock_upr, mock_settings_service, mock_settings
    ):
        """delete_project does not touch Settings/SettingsService when integrations_count is 0."""
        mock_session = MagicMock()
        mock_upr.get_by_project_name.return_value = []
        mock_app_repo.get_project_entity_counts_bulk.return_value = _zero_counts("my-project")

        ProjectService.delete_project(
            session=mock_session,
            project_name="my-project",
            project_type=Application.ProjectType.SHARED,
            actor_id="user-1",
            action="DELETE /v1/projects/my-project",
        )

        mock_settings.get_by_project_names.assert_not_called()
        mock_settings_service.delete_setting.assert_not_called()
        mock_app_repo.delete_by_name.assert_called_once_with(mock_session, "my-project")
```

Leave every other test in the class unchanged — `test_project_with_datasources_raises_409`, `test_project_with_budgets_raises_409`, `test_project_with_active_budget_group_raises_409`, `test_project_with_resources_details_include_non_zero_counts`, `test_empty_project_calls_delete_and_succeeds`, `test_budgets_and_groups_are_detached_before_delete`, etc. must keep passing unmodified — they exercise the non-integrations resource types, which still block.

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/codemie/service/project/test_project_service_delete_update.py -k "integrations" -v`
Expected: FAIL —
- `test_project_with_integrations_deletes_them_then_succeeds` fails because `delete_project` still raises `ExtendedHTTPException(409)` for `integrations_count > 0` (old blocking behavior).
- `test_project_with_zero_integrations_skips_settings_lookup` fails at `@patch("codemie.service.project.project_service.Settings")` / `SettingsService` patch targets not yet referenced that way, or passes trivially but is included here to lock in the no-op path once Step 3 lands — run it alongside to confirm current baseline.

- [ ] **Step 3: Write minimal implementation**

In `src/codemie/service/project/project_service.py`, add the import (alongside the existing `SettingsService` import at line 45):

```python
from codemie.rest_api.models.settings import Settings
```

Replace `_check_has_no_resources` (lines 432-449):

```python
    @classmethod
    def _check_has_no_resources(cls, session: Session, project_name: str, action: str) -> dict:
        """Raise 409 if the project has any blocking resources; integrations are excluded.

        Integrations are handled separately by the caller (auto-deleted, not blocked on).

        Args:
            session: Database session
            project_name: Project name to check
            action: Human-readable action word for the error message ('deleted' or 'renamed')

        Returns:
            The full entity-counts dict for project_name (including integrations_count),
            so the caller can decide whether to auto-delete integrations without a second query.
        """
        entity_counts = application_repository.get_project_entity_counts_bulk(session, [project_name])
        counts = entity_counts.get(project_name, {})
        blocking_counts = {k: v for k, v in counts.items() if k != "integrations_count"}
        if sum(blocking_counts.values()) > 0:
            non_zero = {k: v for k, v in blocking_counts.items() if v > 0}
            raise ExtendedHTTPException(
                code=409,
                message=cls.ERRORS.HAS_RESOURCES.format(name=project_name, action=action),
                details=str(non_zero),
            )
        return counts
```

Update `delete_project` (lines 451-517) — replace the `cls._check_has_no_resources(session, project_name, "deleted")` line and everything from there through `application_repository.delete_by_name(session, project_name)` with:

```python
        counts = cls._check_has_no_resources(session, project_name, "deleted")

        affected_integration_ids: list[str] = []
        if counts.get("integrations_count", 0) > 0:
            project_settings = Settings.get_by_project_names([project_name])
            for setting in project_settings:
                SettingsService.delete_setting(setting.id)
                affected_integration_ids.append(setting.id)

        affected_budget_ids = budget_repository.clear_project_on_deleted_budgets(session, project_name)
        affected_group_ids = project_budget_group_repository.clear_project_on_deleted_groups(session, project_name)

        application_repository.delete_by_name(session, project_name)
```

And extend the `ActivityEventCreate` attributes dict (a few lines below) to include the new field alongside the existing ones:

```python
                attributes={
                    "affected_budgets": affected_budget_ids,
                    "affected_budget_groups": affected_group_ids,
                    "affected_integrations": affected_integration_ids,
                },
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/codemie/service/project/test_project_service_delete_update.py -v`
Expected: PASS — all tests in the file, including the two new ones and every pre-existing 409/success test for other resource types.

- [ ] **Step 5: Run the full project + repository test files together**

Run: `poetry run pytest tests/codemie/service/project/test_project_service_delete_update.py tests/codemie/repository/test_application_repository_entity_counts.py tests/codemie/rest_api/routers/test_projects_router.py -v`
Expected: PASS — confirms the router-level delete tests (which mock `project_service` itself) are unaffected by the service-layer change.

- [ ] **Step 6: Commit**

```bash
git add src/codemie/service/project/project_service.py tests/codemie/service/project/test_project_service_delete_update.py
git commit -m "EPMCDME-14866: Auto-delete project integrations instead of blocking deletion"
```

---

## Self-Review Notes

- **Spec coverage:** Task 1 covers the count/list-parity root-cause fix. Task 2 covers the full desired algorithm (block on other resources, auto-delete integrations, straight-through delete when zero integrations) plus the activity-log addition. Non-atomicity and no-FE-change constraints require no code — verified by scope (no session refactor, no FE files touched).
- **Type consistency:** `_check_has_no_resources` return type changes from `None` to `dict` — only caller is `delete_project` in the same file, updated in the same task.
- **No placeholders:** all steps include literal code/commands.
