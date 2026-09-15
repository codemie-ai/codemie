# EPMCDME-14501: Fix Project Admin Inheritance on Same-Name Project Recreation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the creator's `UserProject` row (and invalidate their RBAC cache and budget allocations) when a project is hard-deleted, so recreating a project with the same name cannot inherit the old Project Admin's access.

**Architecture:** Single insertion point in `ProjectService.delete_project` after `application_repository.delete_by_name`. Three cleanup calls, guarded by `if creator_id:`, following the existing budget-cleanup pattern in the same method. No schema changes, no router changes.

**Tech Stack:** Python, SQLModel, pytest + `unittest.mock.patch`

**Spec:** `docs/superpowers/tasks/2026-09-14-epmcdme-14501-project-admin-deletion/spec.md`

## Global Constraints

- No schema migration — fix is service-layer only.
- No router, API contract, or response model changes.
- All new cleanup calls guarded by `if creator_id:` (the parameter is `str | None`).
- Non-creator assigned-user rows are already blocked at deletion time by the 409 guard — do not touch them here.
- No data backfill for past orphaned rows.

Commit per task using the repository's existing convention.

---

### Task 1: Fix `ProjectService.delete_project` — add creator cleanup

**Files:**
- Modify: `src/codemie/service/project/project_service.py:15-46` (imports), `499-517` (post-delete block)
- Test: `tests/codemie/service/project/test_project_service_delete_update.py`

Test-first: yes — failing assertions: `user_project_repository.remove_project`, `invalidate_user_from_cache`, and `ProjectAssignmentService._sync_project_budget_member_removed` are each called with the creator's id on the success path; today none of those calls exist so the asserts fail immediately.

- [ ] **Step 1: Write the failing tests**

Add a new test method (or extend `test_creator_only_allows_deletion`) in `TestProjectServiceDeleteProject`. The test patches four things and asserts the three new calls:

```python
@patch("codemie.service.project.project_service.ProjectAssignmentService")
@patch("codemie.service.project.project_service.invalidate_user_from_cache")
@patch("codemie.service.project.project_service.user_project_repository")
@patch("codemie.service.project.project_service.application_repository")
def test_delete_cleans_up_creator_membership(
    self, mock_app_repo, mock_upr, mock_invalidate, mock_assignment_svc
):
    mock_session = MagicMock()
    creator_record = MagicMock()
    creator_record.user_id = "creator-1"
    mock_upr.get_by_project_name.return_value = [creator_record]
    mock_app_repo.get_project_entity_counts_bulk.return_value = _zero_counts("my-project")

    ProjectService.delete_project(
        session=mock_session,
        project_name="my-project",
        project_type=Application.ProjectType.SHARED,
        actor_id="creator-1",
        action="DELETE /v1/projects/my-project",
        creator_id="creator-1",
    )

    mock_upr.remove_project.assert_called_once_with(mock_session, "creator-1", "my-project")
    mock_invalidate.assert_called_once_with("creator-1")
    mock_assignment_svc._sync_project_budget_member_removed.assert_called_once_with(
        mock_session, "my-project", "creator-1"
    )
```

- [ ] **Step 2: Run to confirm failure**

```
pytest tests/codemie/service/project/test_project_service_delete_update.py::TestProjectServiceDeleteProject::test_delete_cleans_up_creator_membership -v
```

Expected: FAIL — `remove_project` not called.

- [ ] **Step 3: Add the import in `project_service.py`**

At `src/codemie/service/project/project_service.py:46` (after the existing `invalidate_user_from_cache` import), add:

```python
from codemie.service.project.project_assignment_service import ProjectAssignmentService
```

- [ ] **Step 4: Add the cleanup block in `delete_project`**

In `src/codemie/service/project/project_service.py`, after line 502 (`application_repository.delete_by_name(session, project_name)`), insert:

```python
if creator_id:
    user_project_repository.remove_project(session, creator_id, project_name)
    ProjectAssignmentService._sync_project_budget_member_removed(session, project_name, creator_id)
    invalidate_user_from_cache(creator_id)
```

- [ ] **Step 5: Run to confirm pass**

```
pytest tests/codemie/service/project/test_project_service_delete_update.py -v
```

Expected: all tests pass, including the new one and all pre-existing tests.

---

### Task 2: Add delete-then-same-name-recreate regression test

**Files:**
- Test: `tests/codemie/service/project/test_project_service_delete_update.py`

Test-first: no — task IS the test deliverable; it asserts that the deletion sequence removes the creator's membership row, so a same-named project created afterwards has no inherited `UserProject`.

- [ ] **Step 1: Write the regression test**

Add a new test class at the bottom of `test_project_service_delete_update.py`:

```python
class TestProjectServiceDeleteRecreate:
    """Regression: deleting and recreating a same-named project must not inherit membership."""

    @patch("codemie.service.project.project_service.ProjectAssignmentService")
    @patch("codemie.service.project.project_service.invalidate_user_from_cache")
    @patch("codemie.service.project.project_service.user_project_repository")
    @patch("codemie.service.project.project_service.application_repository")
    def test_old_creator_membership_removed_before_name_reuse(
        self, mock_app_repo, mock_upr, mock_invalidate, mock_assignment_svc
    ):
        """After delete_project, the creator's UserProject row is removed so
        a new project with the same name starts with clean membership."""
        mock_session = MagicMock()
        creator_record = MagicMock()
        creator_record.user_id = "original-admin"
        mock_upr.get_by_project_name.return_value = [creator_record]
        mock_app_repo.get_project_entity_counts_bulk.return_value = _zero_counts("alpha")

        ProjectService.delete_project(
            session=mock_session,
            project_name="alpha",
            project_type=Application.ProjectType.SHARED,
            actor_id="original-admin",
            action="DELETE /v1/projects/alpha",
            creator_id="original-admin",
        )

        # The creator's row must be removed before a same-named project can be created.
        mock_upr.remove_project.assert_called_once_with(
            mock_session, "original-admin", "alpha"
        )
        # Cache must also be invalidated so stale RBAC sessions cannot reflect the
        # old membership on the new project.
        mock_invalidate.assert_called_once_with("original-admin")
```

- [ ] **Step 2: Run to confirm pass**

```
pytest tests/codemie/service/project/test_project_service_delete_update.py::TestProjectServiceDeleteRecreate -v
```

Expected: PASS (Task 1 already implemented the fix).

- [ ] **Step 3: Run full affected test suite to confirm no regression**

```
pytest tests/codemie/service/project/test_project_service_delete_update.py tests/codemie/service/project/test_project_service.py tests/codemie/repository/test_user_project_repository_basic.py tests/codemie/rest_api/routers/test_projects_router.py -v
```

Expected: all pass.

---

## Negative-constraint pass

| Constraint | Honored by |
|---|---|
| No schema migration | Task 1 modifies only `project_service.py` — no Alembic files |
| No router/API changes | Neither task touches `routers/projects.py` or any response model |
| No non-creator cleanup | `if creator_id:` block targets only the creator; non-creator rows are already blocked by the 409 guard |
| No data backfill | No migration scripts or data-fix tasks |
| RBAC key stays `project_name` | No RBAC layer files touched |
| `ProjectAssignmentService` import is intra-package, not a new external dependency | Confirmed — no circular import |

negative-constraints: all honored; none violated by any task.
