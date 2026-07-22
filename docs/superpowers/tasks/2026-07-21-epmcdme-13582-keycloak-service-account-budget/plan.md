# EPMCDME-13582: Budget record not created for service account on first Keycloak login — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ensure `ProjectMemberBudgetAssignment` records are created for service accounts when they log in through Keycloak for the first time, by calling `_sync_project_budget_member_added` after the IDP project bootstrap loop.

**Architecture:** Add a new `_sync_budget_for_idp_projects` static method to `AuthenticationService` that opens an isolated synchronous session per project and calls the existing idempotent budget-sync function. Wire it into `create_user_from_idp` after the project loop, guarded by a top-level try/except so no exception can break login. Update `_sync_project_budget_member_added`'s `actor_id` type hint to `Optional[str]` to match the nullable DB column.

**Tech Stack:** Python, SQLModel (sync Session), pytest, unittest.mock

---

## File Map

| File | Change |
|---|---|
| `src/codemie/service/project/project_assignment_service.py` | Add `from typing import Optional`; change `actor_id: str` → `actor_id: Optional[str]` on line 119 |
| `src/codemie/service/user/authentication_service.py` | Add `_sync_budget_for_idp_projects` static method (~line 267); add try/except call site in `create_user_from_idp` after line 227 |
| `tests/codemie/service/user/test_authentication_service.py` | Update `test_create_user_from_idp_success` to assert budget sync is called; add `test_create_user_from_idp_budget_sync_failure_is_non_fatal` |

---

### Task 1: Fix `actor_id` type hint in `_sync_project_budget_member_added`

**Test-first: No — pure annotation change, no new behavior. Existing tests stay green.**

**Files:**
- Modify: `src/codemie/service/project/project_assignment_service.py:21-24` (imports), `:119` (signature)

- [ ] **Step 1: Add `Optional` import**

  Open `src/codemie/service/project/project_assignment_service.py`. The current imports start at line 21. Add `from typing import Optional` as the first import line (before `import asyncio`):

  ```python
  from typing import Optional

  import asyncio
  from datetime import UTC, datetime, timezone
  ```

- [ ] **Step 2: Update the `actor_id` type hint**

  On line 119, the current signature is:

  ```python
  def _sync_project_budget_member_added(session: Session, project_name: str, user_id: str, actor_id: str) -> None:
  ```

  Change it to:

  ```python
  def _sync_project_budget_member_added(session: Session, project_name: str, user_id: str, actor_id: Optional[str]) -> None:
  ```

- [ ] **Step 3: Run lint to confirm no ruff violations**

  ```bash
  make ruff
  ```

  Expected: exits 0 with no remaining violations.

- [ ] **Step 4: Run the existing project-assignment tests to confirm nothing broke**

  ```bash
  poetry run pytest tests/codemie/service/project/test_project_assignment_service.py -v
  ```

  Expected: all existing tests PASS.

- [ ] **Step 5: Commit**

  ```bash
  git add src/codemie/service/project/project_assignment_service.py
  git commit -m "EPMCDME-13582: Allow None actor_id in _sync_project_budget_member_added"
  ```

---

### Task 2: Write failing tests for the new budget-sync behaviour (RED)

**Test-first: Yes — `test_create_user_from_idp_budget_sync_called` and `test_create_user_from_idp_budget_sync_failure_is_non_fatal` must both FAIL before any implementation.**

**Files:**
- Modify: `tests/codemie/service/user/test_authentication_service.py`

- [ ] **Step 1: Update `test_create_user_from_idp_success` to assert the budget sync helper is called**

  The existing test at line 353 patches `_ensure_projects_exist` and asserts it was called. Add a matching patch for `_sync_budget_for_idp_projects`. Replace the `with (` block (lines 386–394) with:

  ```python
  with (
      patch("codemie.service.user.authentication_service.user_repository") as mock_user_repo,
      patch("codemie.service.user.authentication_service.user_project_repository") as mock_proj_repo,
      patch("codemie.service.user.authentication_service.user_kb_repository") as mock_kb_repo,
      patch(
          "codemie.service.user.authentication_service.AuthenticationService._ensure_projects_exist",
          new_callable=AsyncMock,
      ) as mock_ensure,
      patch(
          "codemie.service.user.authentication_service.AuthenticationService._sync_budget_for_idp_projects",
          new_callable=AsyncMock,
      ) as mock_budget_sync,
      patch("codemie.service.user.authentication_service.config") as mock_config,
  ):
  ```

  Then add this assertion at the end of the test, after the existing `mock_ensure.assert_called_once_with(...)` line:

  ```python
  # Verify budget sync called with correct user_id and project list
  mock_budget_sync.assert_called_once_with(user_id, ["project1", "project2"])
  ```

- [ ] **Step 2: Add the non-fatal failure test**

  After `test_create_user_from_idp_success` (and before `test_create_user_from_idp_invalid_uuid`), insert this new test:

  ```python
  @pytest.mark.asyncio
  async def test_create_user_from_idp_budget_sync_failure_is_non_fatal(self):
      """Budget sync failure does not break first-login — outer try/except guards the call site"""
      # Arrange
      session = AsyncMock()
      user_id = str(uuid4())

      idp_user = security_user.User(
          id=user_id,
          username="svc-account",
          name="Service Account",
          email="svc@example.com",
          picture=None,
          user_type="service",
          roles=[],
          project_names=["project1"],
          admin_project_names=[],
          knowledge_bases=[],
          is_admin=False,
      )

      mock_created_user = UserDB(
          id=user_id,
          email="svc@example.com",
          username="svc-account",
          name="Service Account",
          auth_source="keycloak",
          email_verified=True,
          is_active=True,
          is_admin=False,
      )

      with (
          patch("codemie.service.user.authentication_service.user_repository") as mock_user_repo,
          patch("codemie.service.user.authentication_service.user_project_repository") as mock_proj_repo,
          patch("codemie.service.user.authentication_service.user_kb_repository") as mock_kb_repo,
          patch(
              "codemie.service.user.authentication_service.AuthenticationService._ensure_projects_exist",
              new_callable=AsyncMock,
          ),
          patch(
              "codemie.service.user.authentication_service.AuthenticationService._sync_budget_for_idp_projects",
              new_callable=AsyncMock,
              side_effect=RuntimeError("db unavailable"),
          ),
          patch("codemie.service.user.authentication_service.config") as mock_config,
      ):
          mock_config.IDP_PROVIDER = "keycloak"
          mock_config.ADMIN_USER_ID = "admin-id"
          mock_config.ADMIN_ROLE_NAME = "SuperAdmin"
          mock_user_repo.acreate = AsyncMock(return_value=mock_created_user)
          mock_proj_repo.aadd_project = AsyncMock()
          mock_kb_repo.aadd_kb = AsyncMock()

          # Act — must not raise even though budget sync raises RuntimeError
          result = await AuthenticationService.create_user_from_idp(session, idp_user)

          # Assert — login succeeded despite budget sync failure
          assert result == mock_created_user
  ```

- [ ] **Step 3: Run the new tests to confirm they FAIL (RED)**

  ```bash
  poetry run pytest tests/codemie/service/user/test_authentication_service.py::TestCreateUserFromIdp -v
  ```

  Expected:
  - `test_create_user_from_idp_success` FAILS — `mock_budget_sync.assert_called_once_with` fails because the call site doesn't exist yet.
  - `test_create_user_from_idp_budget_sync_failure_is_non_fatal` FAILS — `_sync_budget_for_idp_projects` doesn't exist, so `patch(...)` raises `AttributeError`.

- [ ] **Step 4: Commit the failing tests**

  ```bash
  git add tests/codemie/service/user/test_authentication_service.py
  git commit -m "EPMCDME-13582: Add failing tests for budget sync on IDP first login"
  ```

---

### Task 3: Implement `_sync_budget_for_idp_projects` and wire the call site (GREEN)

**Test-first: Yes — all tests from Task 2 must PASS after this task.**

**Files:**
- Modify: `src/codemie/service/user/authentication_service.py`

- [ ] **Step 1: Add the `_sync_budget_for_idp_projects` static method**

  In `authentication_service.py`, locate the end of `_ensure_projects_exist` (currently ends around line 266). Insert the new method immediately after it, before the `sync_idp_user_profile` method:

  ```python
  @staticmethod
  async def _sync_budget_for_idp_projects(user_id: str, project_names: list[str]) -> None:
      from codemie.clients.postgres import get_session
      from codemie.service.project.project_assignment_service import ProjectAssignmentService

      for project_name in project_names:
          try:
              with get_session() as session:
                  ProjectAssignmentService._sync_project_budget_member_added(
                      session, project_name, user_id, actor_id=None
                  )
                  session.commit()
          except Exception as e:
              logger.warning(
                  f"budget_sync_skipped_on_idp_bootstrap: user_id={user_id!r} "
                  f"project_name={project_name!r} error={e}",
                  exc_info=True,
              )
  ```

- [ ] **Step 2: Add the call site in `create_user_from_idp`**

  Locate the project bootstrap loop in `create_user_from_idp` (around lines 224–227). After the loop ends (after line 227), before the `# Bootstrap knowledge base access` comment, insert:

  ```python
  # Bootstrap project budget allocations for IDP-provisioned projects (non-fatal)
  try:
      await AuthenticationService._sync_budget_for_idp_projects(db_user.id, idp_user.project_names)
  except Exception as e:
      logger.warning(f"budget_sync_failed_on_idp_bootstrap: user_id={db_user.id!r} error={e}")
  ```

  The updated block in `create_user_from_idp` will look like:

  ```python
  # Bootstrap authorization from idp_user fields (ONE-TIME ONLY)
  for project_name in idp_user.project_names:
      is_project_admin = project_name in idp_user.admin_project_names
      await user_project_repository.aadd_project(session, db_user.id, project_name, is_project_admin)

  # Bootstrap project budget allocations for IDP-provisioned projects (non-fatal)
  try:
      await AuthenticationService._sync_budget_for_idp_projects(db_user.id, idp_user.project_names)
  except Exception as e:
      logger.warning(f"budget_sync_failed_on_idp_bootstrap: user_id={db_user.id!r} error={e}")

  # Bootstrap knowledge base access
  for kb_name in idp_user.knowledge_bases:
      await user_kb_repository.aadd_kb(session, db_user.id, kb_name)
  ```

- [ ] **Step 3: Run the failing tests to confirm they now PASS (GREEN)**

  ```bash
  poetry run pytest tests/codemie/service/user/test_authentication_service.py::TestCreateUserFromIdp -v
  ```

  Expected: all tests in `TestCreateUserFromIdp` PASS, including the two new ones.

- [ ] **Step 4: Run the full authentication service test suite**

  ```bash
  poetry run pytest tests/codemie/service/user/test_authentication_service.py -v
  ```

  Expected: all tests PASS.

- [ ] **Step 5: Run lint**

  ```bash
  make ruff
  ```

  Expected: exits 0.

- [ ] **Step 6: Commit the implementation**

  ```bash
  git add src/codemie/service/user/authentication_service.py
  git commit -m "EPMCDME-13582: Sync project budget on IDP first-login project bootstrap"
  ```
