# Default Project Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a per-membership `is_default` flag to `user_projects`, with set/clear endpoints gated to admin-or-maintainer, so downstream consumers have one authoritative "which project is this user's default" answer.

**Architecture:** A boolean column on the existing `UserProject` row (same table, same pattern as `is_project_admin`), a DB partial-unique-index backstop for "at most one default per user," two new endpoints on the existing `user_management_router.py` (not `projects.py` — that router serves a different, budget-integrated membership path), backed by two new `UserAccessService` methods that reuse the existing `_reject_if_personal_project` guard. Read-side exposure via the two existing per-project response models, propagated at their five real construction call sites.

**Tech Stack:** FastAPI, SQLModel (sync `Session`), Alembic, pytest with `unittest.mock.MagicMock`/`@patch` (this codebase's existing unit-test style for this service/repository — no DB fixtures used here).

**Spec:** `docs/superpowers/tasks/2026-09-23-epmcdme-15110-default-project-foundation/spec.md`

## Global Constraints

- New endpoints gate on `admin_or_maintainer_access_only` (platform-level role) — **never** on `is_project_admin` (the per-project flag on the same row). This is an explicit ticket requirement (project admins are excluded even for their own project).
- No new package dependencies.
- No backfill migration — new column defaults to `False` for all existing rows (explicit out-of-scope item).
- `ProjectAccessRequest` (add-member) and `ProjectAccessUpdateRequest` (role-toggle) request models are **not** extended — set/clear-default are separate endpoints (spec decision, Section "Not touched").

---

## Important correction versus the approved spec

The spec's "API surface" section named `ProjectAssignmentService` as the service to extend. Research during planning found this is **wrong** — that service (`src/codemie/service/project/project_assignment_service.py`) backs the `projects.py` router's budget-integrated, async membership path. The actual "Settings → Administration → Users" screen the ticket describes (per-user details panel, per-row role selector, Add Project dialog) is served by `user_management_router.py`, backed by the **sync** `UserAccessService` (`src/codemie/service/user/user_access_service.py`), which has its own separate `_reject_if_personal_project` implementation with a different signature. This plan targets `UserAccessService` / `user_management_router.py`. The underlying design (column-on-row, dedicated endpoints, admin-or-maintainer gating, automatic clear-on-removal) is unchanged — only the concrete file targets move. Both services ultimately read/write the same `UserProject` row via `UserProjectRepository`, so the "removal clears default for free" property holds regardless of which service performs the delete.

---

### Task 1: Schema — `is_default` column and partial unique index

**Files:**
- Modify: `src/codemie/rest_api/models/user_management.py:22-24` (imports), `:62-73` (`UserProject` class)
- Create: `src/external/alembic/versions/<new_revision_id>_add_is_default_to_user_projects.py`

**Interfaces:**
- Produces: `UserProject.is_default: bool` (default `False`), enforced unique-when-true per `user_id` at the DB layer.

- [ ] **Step 1: Add the column to the model**

Edit `src/codemie/rest_api/models/user_management.py`. Current imports (line 22-24):

```python
from pydantic import BaseModel, EmailStr, Field, model_validator
from sqlalchemy import UniqueConstraint
from sqlmodel import Field as SQLField, SQLModel
```

Change to:

```python
from pydantic import BaseModel, EmailStr, Field, model_validator
from sqlalchemy import Index, UniqueConstraint, text
from sqlmodel import Field as SQLField, SQLModel
```

Current `UserProject` class (lines 62-73):

```python
class UserProject(BaseModelWithSQLSupport, table=True):
    """User-to-project access mapping"""

    __tablename__ = "user_projects"

    id: str = SQLField(default_factory=lambda: str(uuid4()), primary_key=True)
    user_id: str = SQLField(foreign_key=_USERS_ID_FK, index=True, nullable=False)
    project_name: str = SQLField(index=True, nullable=False)
    is_project_admin: bool = SQLField(default=False)
    # Using date inherited from CommonBaseModel (no created_at)

    __table_args__ = (UniqueConstraint('user_id', 'project_name', name='uix_user_project'),)
```

Replace with:

```python
class UserProject(BaseModelWithSQLSupport, table=True):
    """User-to-project access mapping"""

    __tablename__ = "user_projects"

    id: str = SQLField(default_factory=lambda: str(uuid4()), primary_key=True)
    user_id: str = SQLField(foreign_key=_USERS_ID_FK, index=True, nullable=False)
    project_name: str = SQLField(index=True, nullable=False)
    is_project_admin: bool = SQLField(default=False)
    is_default: bool = SQLField(default=False)
    # Using date inherited from CommonBaseModel (no created_at)

    __table_args__ = (
        UniqueConstraint('user_id', 'project_name', name='uix_user_project'),
        Index(
            "uix_user_projects_one_default",
            "user_id",
            unique=True,
            postgresql_where=text("is_default = true"),
        ),
    )
```

- [ ] **Step 2: Get the current alembic head**

This repo has multiple divergent alembic branch heads today (confirmed during planning — `grep`-ing all `Revises:` values against all `Revision ID:` values found 32 files with no child, i.e. 32 unmerged heads). Do not guess a `down_revision`. Run:

```bash
cd /d/Projects/codemie && poetry run alembic heads
```

If more than one head is printed, ask which one this migration should chain from (normally the most recently merged one on `main` for this table family — search `git log --oneline -- src/external/alembic/versions | head -5` for the most recent migration touching `user_projects` or `user_management` and use its revision id). Use that value as `down_revision` below.

- [ ] **Step 3: Write the migration**

Create `src/external/alembic/versions/<new_revision_id>_add_is_default_to_user_projects.py` (pick a 12-char lowercase-hex revision id not already used, e.g. via `python -c "import uuid; print(uuid.uuid4().hex[:12])"`):

```python
"""add_is_default_to_user_projects

Revision ID: <new_revision_id>
Revises: <head_from_step_2>
Create Date: 2026-09-23 00:00:00.000000

Adds the per-membership default-project flag (EPMCDME-15110). Additive only,
no backfill: every existing row defaults to is_default=false. A partial
unique index enforces at most one default per user at the DB layer,
backstopping the application-level swap-old-for-new transaction.
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "<new_revision_id>"
down_revision = "<head_from_step_2>"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "user_projects",
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index(
        "uix_user_projects_one_default",
        "user_projects",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("is_default = true"),
    )


def downgrade() -> None:
    op.drop_index("uix_user_projects_one_default", table_name="user_projects")
    op.drop_column("user_projects", "is_default")
```

- [ ] **Step 4: Verify the migration runs**

Run: `cd /d/Projects/codemie && poetry run alembic upgrade head`
Expected: migration applies with no error; `\d user_projects` (via `psql`) shows the new `is_default` column and the `uix_user_projects_one_default` partial index.

- [ ] **Step 5: Commit**

```bash
git add src/codemie/rest_api/models/user_management.py src/external/alembic/versions/<new_revision_id>_add_is_default_to_user_projects.py
git commit -m "EPMCDME-15110: Add is_default column to user_projects"
```

---

### Task 2: Repository — set/clear/get-default methods

**Files:**
- Modify: `src/codemie/repository/user_project_repository.py:109-126` (add methods near `remove_project`/`update_admin_status`)
- Test: `tests/codemie/repository/test_user_project_repository_default.py` (new file)

**Interfaces:**
- Consumes: `UserProject` model with `is_default: bool` (Task 1).
- Produces: `user_project_repository.get_default_for_user(session, user_id) -> UserProject | None`, `user_project_repository.set_default(session, user_id, project_name) -> UserProject | None` (returns `None` if the target membership doesn't exist — no write happens), `user_project_repository.clear_default(session, user_id, project_name) -> UserProject | None` (returns `None` if the target membership doesn't exist).

- [ ] **Step 1: Write the failing tests**

Create `tests/codemie/repository/test_user_project_repository_default.py`:

```python
# Copyright 2026 EPAM Systems, Inc. (“EPAM”)
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Tests for is_default set/clear/get methods on user_project_repository (EPMCDME-15110)."""

from unittest.mock import MagicMock

from codemie.repository.user_project_repository import user_project_repository
from codemie.rest_api.models.user_management import UserProject


class TestGetDefaultForUser:
    def test_returns_default_project(self):
        mock_session = MagicMock()
        default_project = UserProject(id="up1", user_id="user-1", project_name="proj-a", is_default=True)
        mock_session.exec.return_value.first.return_value = default_project

        result = user_project_repository.get_default_for_user(mock_session, "user-1")

        assert result is default_project

    def test_returns_none_when_no_default(self):
        mock_session = MagicMock()
        mock_session.exec.return_value.first.return_value = None

        result = user_project_repository.get_default_for_user(mock_session, "user-1")

        assert result is None


class TestSetDefault:
    def test_returns_none_when_membership_missing(self):
        mock_session = MagicMock()
        mock_session.exec.return_value.first.return_value = None

        result = user_project_repository.set_default(mock_session, "user-1", "proj-a")

        assert result is None
        mock_session.add.assert_not_called()

    def test_sets_default_and_unsets_previous(self):
        mock_session = MagicMock()
        target = UserProject(id="up-target", user_id="user-1", project_name="proj-a", is_default=False)
        previous_default = UserProject(id="up-prev", user_id="user-1", project_name="proj-b", is_default=True)

        # First exec() call = lookup target row; second = lookup existing default row.
        mock_session.exec.return_value.first.side_effect = [target, previous_default]

        result = user_project_repository.set_default(mock_session, "user-1", "proj-a")

        assert result is target
        assert target.is_default is True
        assert previous_default.is_default is False
        assert mock_session.add.call_count == 2  # previous_default flip + target flip

    def test_idempotent_when_already_default(self):
        mock_session = MagicMock()
        target = UserProject(id="up-target", user_id="user-1", project_name="proj-a", is_default=True)
        # First exec() call = lookup target row; second = lookup existing default row (itself).
        mock_session.exec.return_value.first.side_effect = [target, target]

        result = user_project_repository.set_default(mock_session, "user-1", "proj-a")

        assert result is target
        assert target.is_default is True


class TestClearDefault:
    def test_returns_none_when_membership_missing(self):
        mock_session = MagicMock()
        mock_session.exec.return_value.first.return_value = None

        result = user_project_repository.clear_default(mock_session, "user-1", "proj-a")

        assert result is None

    def test_clears_default(self):
        mock_session = MagicMock()
        target = UserProject(id="up-target", user_id="user-1", project_name="proj-a", is_default=True)
        mock_session.exec.return_value.first.return_value = target

        result = user_project_repository.clear_default(mock_session, "user-1", "proj-a")

        assert result is target
        assert target.is_default is False

    def test_noop_when_not_currently_default(self):
        mock_session = MagicMock()
        target = UserProject(id="up-target", user_id="user-1", project_name="proj-a", is_default=False)
        mock_session.exec.return_value.first.return_value = target

        result = user_project_repository.clear_default(mock_session, "user-1", "proj-a")

        assert result is target
        assert target.is_default is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /d/Projects/codemie && poetry run pytest tests/codemie/repository/test_user_project_repository_default.py -v`
Expected: FAIL with `AttributeError: 'UserProjectRepository' object has no attribute 'get_default_for_user'` (and similarly for `set_default`/`clear_default`).

- [ ] **Step 3: Implement the methods**

In `src/codemie/repository/user_project_repository.py`, insert after `remove_project` (after line 126, before `update_admin_status`):

```python
    def get_default_for_user(self, session: Session, user_id: str) -> Optional[UserProject]:
        """Get the user's current default-project membership row, if any.

        Args:
            session: Database session
            user_id: User UUID

        Returns:
            UserProject with is_default=True, or None if the user has no default.
        """
        statement = select(UserProject).where(UserProject.user_id == user_id, UserProject.is_default)
        return session.exec(statement).first()

    def set_default(self, session: Session, user_id: str, project_name: str) -> Optional[UserProject]:
        """Mark (user_id, project_name) as the user's default, unsetting any prior default.

        No-op (idempotent) if the target row is already the default. Does not write anything
        if the target membership does not exist.

        Args:
            session: Database session
            user_id: User UUID
            project_name: Project name

        Returns:
            The updated UserProject row, or None if the user has no membership in project_name.
        """
        target = self.get_by_user_and_project(session, user_id, project_name)
        if not target:
            return None

        previous_default = self.get_default_for_user(session, user_id)
        if previous_default is not None and previous_default.id != target.id:
            previous_default.is_default = False
            previous_default.update_date = datetime.now(UTC)
            session.add(previous_default)

        if not target.is_default:
            target.is_default = True
            target.update_date = datetime.now(UTC)
            session.add(target)

        session.flush()
        session.refresh(target)
        return target

    def clear_default(self, session: Session, user_id: str, project_name: str) -> Optional[UserProject]:
        """Unset the default flag on (user_id, project_name) if currently set.

        No-op if the row exists but isn't currently the default. Returns None if the
        membership does not exist at all.

        Args:
            session: Database session
            user_id: User UUID
            project_name: Project name

        Returns:
            The (possibly unchanged) UserProject row, or None if the membership doesn't exist.
        """
        target = self.get_by_user_and_project(session, user_id, project_name)
        if not target:
            return None

        if target.is_default:
            target.is_default = False
            target.update_date = datetime.now(UTC)
            session.add(target)
            session.flush()
            session.refresh(target)

        return target
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /d/Projects/codemie && poetry run pytest tests/codemie/repository/test_user_project_repository_default.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
git add src/codemie/repository/user_project_repository.py tests/codemie/repository/test_user_project_repository_default.py
git commit -m "EPMCDME-15110: Add set/clear/get-default repository methods"
```

---

### Task 3: Service — set/clear default with permission-neutral guard reuse

**Files:**
- Modify: `src/codemie/service/user/user_access_service.py:130-153` (insert new methods near `update_user_project_access`/`revoke_project_access`)
- Test: `tests/codemie/service/user/test_user_access_service_default_project.py` (new file)

**Interfaces:**
- Consumes: `user_project_repository.set_default`, `.clear_default` (Task 2); `UserAccessService._reject_if_personal_project(session, project_name, actor, target_user_id, action)` (existing, unchanged).
- Produces: `UserAccessService.set_default_project(user_id: str, project_name: str, actor: User) -> dict[str, str]`, `UserAccessService.clear_default_project(user_id: str, project_name: str, actor: User) -> dict[str, str]`. Both raise `ExtendedHTTPException(code=404, ...)` when the target user or membership doesn't exist.

- [ ] **Step 1: Write the failing tests**

Create `tests/codemie/service/user/test_user_access_service_default_project.py`:

```python
# Copyright 2026 EPAM Systems, Inc. (“EPAM”)
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Tests for UserAccessService.set_default_project / clear_default_project (EPMCDME-15110)."""

from unittest.mock import MagicMock, patch

import pytest

from codemie.core.exceptions import ExtendedHTTPException
from codemie.service.user.user_access_service import UserAccessService


class TestSetDefaultProject:
    @patch("codemie.clients.postgres.get_session")
    @patch("codemie.service.user.user_access_service.user_project_repository")
    @patch("codemie.service.user.user_access_service.application_repository")
    @patch("codemie.service.user.user_access_service.user_repository")
    def test_sets_default_for_existing_membership(
        self, mock_user_repo, mock_app_repo, mock_upr, mock_get_session
    ):
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session
        mock_user_repo.get_by_id.return_value = MagicMock(id="user-1")
        mock_app_repo.get_by_name.return_value = MagicMock(project_type="shared")
        mock_upr.set_default.return_value = MagicMock(user_id="user-1", project_name="proj-a", is_default=True)

        result = UserAccessService.set_default_project(
            user_id="user-1",
            project_name="proj-a",
            actor=MagicMock(id="admin-1", is_admin_or_maintainer=True),
        )

        assert result == {"message": "Default project set successfully"}
        mock_upr.set_default.assert_called_once_with(mock_session, "user-1", "proj-a")

    @patch("codemie.clients.postgres.get_session")
    @patch("codemie.service.user.user_access_service.user_project_repository")
    @patch("codemie.service.user.user_access_service.application_repository")
    @patch("codemie.service.user.user_access_service.user_repository")
    def test_rejects_when_not_a_member(self, mock_user_repo, mock_app_repo, mock_upr, mock_get_session):
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session
        mock_user_repo.get_by_id.return_value = MagicMock(id="user-1")
        mock_app_repo.get_by_name.return_value = MagicMock(project_type="shared")
        mock_upr.set_default.return_value = None  # repository: membership doesn't exist

        with pytest.raises(ExtendedHTTPException) as exc_info:
            UserAccessService.set_default_project(
                user_id="user-1",
                project_name="proj-not-a-member",
                actor=MagicMock(id="admin-1", is_admin_or_maintainer=True),
            )

        assert exc_info.value.code == 404
        mock_upr.set_default.assert_called_once()

    @patch("codemie.clients.postgres.get_session")
    @patch("codemie.service.user.user_access_service.user_repository")
    def test_rejects_unknown_user(self, mock_user_repo, mock_get_session):
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session
        mock_user_repo.get_by_id.return_value = None

        with pytest.raises(ExtendedHTTPException) as exc_info:
            UserAccessService.set_default_project(
                user_id="ghost", project_name="proj-a", actor=MagicMock(id="admin-1")
            )

        assert exc_info.value.code == 404

    @patch("codemie.clients.postgres.get_session")
    @patch("codemie.service.user.user_access_service.user_repository")
    @patch("codemie.service.user.user_access_service.application_repository")
    def test_rejects_on_personal_project(self, mock_app_repo, mock_user_repo, mock_get_session):
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session
        mock_user_repo.get_by_id.return_value = MagicMock(id="user-1")
        mock_app_repo.get_by_name.return_value = MagicMock(project_type="personal", created_by="other-user")

        with pytest.raises(ExtendedHTTPException) as exc_info:
            UserAccessService.set_default_project(
                user_id="user-1",
                project_name="alice@example.com",
                actor=MagicMock(id="admin-1", is_admin_or_maintainer=False),
            )

        assert exc_info.value.code == 404  # hides project existence, same as existing grant/update/revoke


class TestClearDefaultProject:
    @patch("codemie.clients.postgres.get_session")
    @patch("codemie.service.user.user_access_service.user_project_repository")
    @patch("codemie.service.user.user_access_service.application_repository")
    @patch("codemie.service.user.user_access_service.user_repository")
    def test_clears_default(self, mock_user_repo, mock_app_repo, mock_upr, mock_get_session):
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session
        mock_user_repo.get_by_id.return_value = MagicMock(id="user-1")
        mock_app_repo.get_by_name.return_value = MagicMock(project_type="shared")
        mock_upr.clear_default.return_value = MagicMock(user_id="user-1", project_name="proj-a", is_default=False)

        result = UserAccessService.clear_default_project(
            user_id="user-1", project_name="proj-a", actor=MagicMock(id="admin-1", is_admin_or_maintainer=True)
        )

        assert result == {"message": "Default project cleared successfully"}

    @patch("codemie.clients.postgres.get_session")
    @patch("codemie.service.user.user_access_service.user_project_repository")
    @patch("codemie.service.user.user_access_service.application_repository")
    @patch("codemie.service.user.user_access_service.user_repository")
    def test_rejects_when_not_a_member(self, mock_user_repo, mock_app_repo, mock_upr, mock_get_session):
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session
        mock_user_repo.get_by_id.return_value = MagicMock(id="user-1")
        mock_app_repo.get_by_name.return_value = MagicMock(project_type="shared")
        mock_upr.clear_default.return_value = None

        with pytest.raises(ExtendedHTTPException) as exc_info:
            UserAccessService.clear_default_project(
                user_id="user-1", project_name="proj-not-a-member", actor=MagicMock(id="admin-1")
            )

        assert exc_info.value.code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /d/Projects/codemie && poetry run pytest tests/codemie/service/user/test_user_access_service_default_project.py -v`
Expected: FAIL with `AttributeError: type object 'UserAccessService' has no attribute 'set_default_project'`

- [ ] **Step 3: Implement the methods**

In `src/codemie/service/user/user_access_service.py`, add imports for the repository (already imported: `user_project_repository` at line 27 — confirm; if the exact import line differs, add `from codemie.repository.user_project_repository import user_project_repository` alongside the existing repository imports). Insert after `update_user_project_access` (after line 128, before `revoke_project_access`):

```python
    @staticmethod
    def set_default_project(user_id: str, project_name: str, actor: User) -> dict[str, str]:
        """Mark project_name as user_id's default project, unsetting any prior default."""
        from codemie.clients.postgres import get_session

        with get_session() as session:
            target_user = user_repository.get_by_id(session, user_id)
            if not target_user:
                raise ExtendedHTTPException(code=404, message=_ERRORS.USER_NOT_FOUND)

            UserAccessService._reject_if_personal_project(
                session, project_name, actor, user_id, "set_default_project"
            )

            updated = user_project_repository.set_default(session, user_id, project_name)
            if not updated:
                raise ExtendedHTTPException(code=404, message="User does not have access to this project")

            session.commit()

            log_details = UserAccessService._build_project_access_log_details(actor.id, user_id)
            logger.info(f"default_project_set: {log_details}, domain=user_management")

            return {"message": "Default project set successfully"}

    @staticmethod
    def clear_default_project(user_id: str, project_name: str, actor: User) -> dict[str, str]:
        """Unset project_name as user_id's default project, if currently set."""
        from codemie.clients.postgres import get_session

        with get_session() as session:
            target_user = user_repository.get_by_id(session, user_id)
            if not target_user:
                raise ExtendedHTTPException(code=404, message=_ERRORS.USER_NOT_FOUND)

            UserAccessService._reject_if_personal_project(
                session, project_name, actor, user_id, "clear_default_project"
            )

            updated = user_project_repository.clear_default(session, user_id, project_name)
            if not updated:
                raise ExtendedHTTPException(code=404, message="User does not have access to this project")

            session.commit()

            log_details = UserAccessService._build_project_access_log_details(actor.id, user_id)
            logger.info(f"default_project_cleared: {log_details}, domain=user_management")

            return {"message": "Default project cleared successfully"}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /d/Projects/codemie && poetry run pytest tests/codemie/service/user/test_user_access_service_default_project.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add src/codemie/service/user/user_access_service.py tests/codemie/service/user/test_user_access_service_default_project.py
git commit -m "EPMCDME-15110: Add set/clear default-project service methods"
```

---

### Task 4: Router — set/clear default endpoints with permission gating

**Files:**
- Modify: `src/codemie/rest_api/routers/user_management_router.py:42-48` (import `admin_or_maintainer_access_only`), `:320-352` (insert new routes after `remove_project_access`)
- Test: `tests/codemie/rest_api/routers/test_user_management_router_default_project.py` (new file)

**Interfaces:**
- Consumes: `UserAccessService.set_default_project`, `.clear_default_project` (Task 3); `admin_or_maintainer_access_only` (existing dependency, `src/codemie/rest_api/security/authentication.py:217`).
- Produces: `PUT /v1/admin/users/{user_id}/projects/{project_name}/default`, `DELETE /v1/admin/users/{user_id}/projects/{project_name}/default` (exact path prefix matches whatever `router` is mounted under in this file — same prefix as the existing `/{user_id}/projects/{project_name}` routes).

**Note on test style in this codebase**: this router's tests do **not** use an HTTP `TestClient`. They call the endpoint functions directly as plain Python functions (e.g. `update_project_access("user-456", "demo-project", request_data, admin_user, None)`, confirmed at `test_user_management_router_crud.py:927`), passing `None` for the `Depends(...)` parameter since direct calls bypass FastAPI's dependency injection. Role-rejection (403) behavior belongs to the shared `admin_or_maintainer_access_only` dependency itself, which is exercised by its own existing tests elsewhere — these new tests only need to prove correct wiring (the endpoint calls the right service method with the right arguments), matching exactly what `test_update_project_access_success` already does for the existing sibling endpoint.

- [ ] **Step 1: Write the failing tests**

Create `tests/codemie/rest_api/routers/test_user_management_router_default_project.py`:

```python
# Copyright 2026 EPAM Systems, Inc. (“EPAM”)
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Tests for set_default_project / clear_default_project endpoint functions (EPMCDME-15110).

Calls the endpoint functions directly (this router's established test style — see
test_user_management_router_crud.py:927), passing None for the Depends(...) parameter.
Role-rejection behavior belongs to the shared admin_or_maintainer_access_only dependency,
already covered by its own tests; these tests prove correct service wiring only.
"""

from unittest.mock import patch

import pytest

from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.routers.user_management_router import clear_default_project, set_default_project
from codemie.rest_api.security.user import User


@pytest.fixture
def admin_user():
    return User(
        id="admin-1",
        email="admin@example.com",
        username="admin",
        name="Admin",
        is_admin=True,
        is_maintainer=False,
        is_auditor=False,
        project_names=["demo"],
        admin_project_names=[],
    )


class TestSetDefaultProjectEndpoint:
    @patch("codemie.rest_api.routers.user_management_router.config")
    @patch("codemie.rest_api.routers.user_management_router.user_access_service")
    def test_calls_service_with_correct_args(self, mock_service, mock_config, admin_user):
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_service.set_default_project.return_value = {"message": "Default project set successfully"}

        result = set_default_project("user-456", "demo-project", admin_user, None)

        assert result == {"message": "Default project set successfully"}
        mock_service.set_default_project.assert_called_once_with(
            user_id="user-456", project_name="demo-project", actor=admin_user
        )

    @patch("codemie.rest_api.routers.user_management_router.config")
    def test_disabled_management_returns_400(self, mock_config, admin_user):
        mock_config.ENABLE_USER_MANAGEMENT = False

        with pytest.raises(ExtendedHTTPException) as exc_info:
            set_default_project("user-456", "demo-project", admin_user, None)

        assert exc_info.value.code == 400


class TestClearDefaultProjectEndpoint:
    @patch("codemie.rest_api.routers.user_management_router.config")
    @patch("codemie.rest_api.routers.user_management_router.user_access_service")
    def test_calls_service_with_correct_args(self, mock_service, mock_config, admin_user):
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_service.clear_default_project.return_value = {"message": "Default project cleared successfully"}

        result = clear_default_project("user-456", "demo-project", admin_user, None)

        assert result == {"message": "Default project cleared successfully"}
        mock_service.clear_default_project.assert_called_once_with(
            user_id="user-456", project_name="demo-project", actor=admin_user
        )

    @patch("codemie.rest_api.routers.user_management_router.config")
    def test_disabled_management_returns_400(self, mock_config, admin_user):
        mock_config.ENABLE_USER_MANAGEMENT = False

        with pytest.raises(ExtendedHTTPException) as exc_info:
            clear_default_project("user-456", "demo-project", admin_user, None)

        assert exc_info.value.code == 400
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /d/Projects/codemie && poetry run pytest tests/codemie/rest_api/routers/test_user_management_router_default_project.py -v`
Expected: FAIL with `ImportError: cannot import name 'set_default_project'` (route function doesn't exist yet)

- [ ] **Step 3: Implement the endpoints**

In `src/codemie/rest_api/routers/user_management_router.py`, update the import block (lines 42-48):

```python
from codemie.rest_api.security.authentication import (
    authenticate,
    admin_access_only,
    admin_or_maintainer_access_only,
    admin_or_maintainer_or_auditor_access,
    maintainer_access_only,
    project_admin_or_admin_user_detail_access,
)
```

Insert after `remove_project_access` (after line 351, before the Knowledge Base section comment):

```python
@router.put("/{user_id}/projects/{project_name}/default")
def set_default_project(
    user_id: str,
    project_name: str,
    user: User = Depends(authenticate),
    _: None = Depends(admin_or_maintainer_access_only),
):
    """Mark project_name as the user's default project.

    Admin or maintainer only. Rejects if the user is not a member of project_name.
    """
    if not config.ENABLE_USER_MANAGEMENT:
        raise ExtendedHTTPException(code=400, message=_USER_MGMT_NOT_ENABLED)

    return user_access_service.set_default_project(user_id=user_id, project_name=project_name, actor=user)


@router.delete("/{user_id}/projects/{project_name}/default")
def clear_default_project(
    user_id: str,
    project_name: str,
    user: User = Depends(authenticate),
    _: None = Depends(admin_or_maintainer_access_only),
):
    """Unset project_name as the user's default project, if currently set.

    Admin or maintainer only.
    """
    if not config.ENABLE_USER_MANAGEMENT:
        raise ExtendedHTTPException(code=400, message=_USER_MGMT_NOT_ENABLED)

    return user_access_service.clear_default_project(user_id=user_id, project_name=project_name, actor=user)
```

`user_access_service` is already imported and used by the sibling endpoints in this file (e.g. `add_project_access` calls `user_access_service.grant_project_access(...)`) — no new import needed for the service singleton.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /d/Projects/codemie && poetry run pytest tests/codemie/rest_api/routers/test_user_management_router_default_project.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/codemie/rest_api/routers/user_management_router.py tests/codemie/rest_api/routers/test_user_management_router_default_project.py
git commit -m "EPMCDME-15110: Add set/clear default-project endpoints"
```

---

### Task 5: Read side — propagate `is_default` through response models

**Files:**
- Modify: `src/codemie/rest_api/models/user_management.py:244-249` (`ProjectInfo`), `:307-312` (`AdminUserProject`)
- Modify: `src/codemie/service/user/user_management_service.py:193`, `:377`
- Modify: `src/codemie/service/user/registration_service.py:265`
- Modify: `src/codemie/service/user/authentication_service.py:732`
- Modify: `src/codemie/service/user/user_access_service.py` (the dict literal in `get_user_projects_list`, lines 71-74)
- Test: extend `tests/codemie/service/user/test_user_access_service.py` (existing file — add one test to the class covering `get_user_projects_list`) and add one assertion each to any existing tests in `user_management_service`/`registration_service`/`authentication_service` test files that already assert on `ProjectInfo` construction, if such assertions exist; otherwise add a focused new test per file as shown below.

**Interfaces:**
- Consumes: `UserProject.is_default` (Task 1).
- Produces: `ProjectInfo.is_default: bool`, `AdminUserProject.is_default: bool`, both populated from the real row instead of defaulting silently to `False` for every user.

- [ ] **Step 1: Write the failing test**

This task's risk is exactly "field added to the model, but never populated at any of the 5 call sites" (flagged in `technical-analysis.md`). One targeted regression test per call site is enough; add this test to `tests/codemie/service/user/test_user_access_service.py` (open the file first to match its existing class/fixture style, then add):

```python
class TestGetUserProjectsListDefaultField:
    @patch("codemie.clients.postgres.get_session")
    @patch("codemie.service.user.user_access_service.user_project_repository")
    @patch("codemie.service.user.user_access_service.user_repository")
    def test_includes_is_default_in_projects_list(self, mock_user_repo, mock_upr, mock_get_session):
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session
        mock_user_repo.get_by_id.return_value = MagicMock(id="user-1")
        mock_upr.get_by_user_id.return_value = [
            MagicMock(project_name="proj-a", is_project_admin=False, is_default=True, date=None),
        ]

        result = UserAccessService.get_user_projects_list("user-1")

        assert result["projects"][0]["is_default"] is True
```

(Add the matching `from unittest.mock import MagicMock, patch` and `from codemie.service.user.user_access_service import UserAccessService` imports if the file doesn't already import them at module scope — check the existing file first.)

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /d/Projects/codemie && poetry run pytest tests/codemie/service/user/test_user_access_service.py -k test_includes_is_default_in_projects_list -v`
Expected: FAIL with `KeyError: 'is_default'`

- [ ] **Step 3: Add the field to both response models**

In `src/codemie/rest_api/models/user_management.py`, `ProjectInfo` (lines 244-249):

```python
class ProjectInfo(BaseModel):
    """Project access information for user responses"""

    name: str
    display_name: Optional[str] = None
    is_project_admin: bool
    is_default: bool = False
```

`AdminUserProject` (lines 307-312):

```python
class AdminUserProject(BaseModel):
    """Project access details for admin view"""

    project_name: str
    is_project_admin: bool
    is_default: bool = False
    date: Optional[datetime]  # Creation timestamp (from CommonBaseModel)
```

- [ ] **Step 4: Propagate at every construction call site**

`src/codemie/service/user/user_access_service.py`, in `get_user_projects_list` (lines 70-75):

```python
            return {
                "projects": [
                    {
                        "project_name": p.project_name,
                        "is_project_admin": p.is_project_admin,
                        "is_default": p.is_default,
                        "date": p.date,
                    }
                    for p in projects
                ]
            }
```

`src/codemie/service/user/user_management_service.py:193`:

```python
        projects = [
            ProjectInfo(name=up.project_name, is_project_admin=up.is_project_admin, is_default=up.is_default)
            for up in visible_projects
        ]
```

`src/codemie/service/user/user_management_service.py:377` (identical pattern, same `up` loop variable — confirmed during planning):

```python
                    ProjectInfo(name=up.project_name, is_project_admin=up.is_project_admin, is_default=up.is_default)
```

`src/codemie/service/user/registration_service.py:265`:

```python
                        ProjectInfo(name=p.project_name, is_project_admin=p.is_project_admin, is_default=p.is_default)
                        for p in user_projects
```

`src/codemie/service/user/authentication_service.py:732`:

```python
                ProjectInfo(name=p.project_name, is_project_admin=p.is_project_admin, is_default=p.is_default)
                for p in user_projects
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd /d/Projects/codemie && poetry run pytest tests/codemie/service/user/test_user_access_service.py -k test_includes_is_default_in_projects_list -v`
Expected: PASS

- [ ] **Step 6: Run the full existing test suite for all five touched files to catch regressions**

Run: `cd /d/Projects/codemie && poetry run pytest tests/codemie/service/user/ tests/codemie/rest_api/routers/test_user_management_router_crud.py -v`
Expected: PASS — `ProjectInfo`/`AdminUserProject` now require `is_default` to be resolvable at every construction site; any existing test that constructs one of these models directly without `is_default` will still pass because the field defaults to `False`, but any existing test asserting equality against a full `ProjectInfo`/`AdminUserProject` instance may need `is_default=False` added to its expected value — fix any such failures inline.

- [ ] **Step 7: Commit**

```bash
git add src/codemie/rest_api/models/user_management.py src/codemie/service/user/user_access_service.py src/codemie/service/user/user_management_service.py src/codemie/service/user/registration_service.py src/codemie/service/user/authentication_service.py tests/codemie/service/user/test_user_access_service.py
git commit -m "EPMCDME-15110: Propagate is_default through project read models"
```

---

### Task 6: Regression — removal (single and bulk) still clears the default with zero new code

**Files:**
- Test: `tests/codemie/repository/test_user_project_repository_default.py` (extend from Task 2)

**Interfaces:**
- Consumes: `user_project_repository.remove_project`, `.remove_projects_for_users` (existing, unchanged).
- Produces: nothing new — this task is proof, not implementation, that the design's central claim (AC3) holds.

- [ ] **Step 1: Write the proof tests**

Append to `tests/codemie/repository/test_user_project_repository_default.py`:

```python
class TestDefaultClearedOnRemoval:
    """Proves AC3: is_default lives on the row, so both removal paths clear it for free."""

    def test_single_removal_clears_default(self):
        mock_session = MagicMock()
        default_row = UserProject(id="up1", user_id="user-1", project_name="proj-a", is_default=True)
        mock_session.exec.return_value.first.return_value = default_row

        removed = user_project_repository.remove_project(mock_session, "user-1", "proj-a")

        assert removed is True
        mock_session.delete.assert_called_once_with(default_row)  # the whole row, flag included, is gone

    def test_bulk_removal_clears_default(self):
        mock_session = MagicMock()
        default_row = UserProject(id="up1", user_id="user-1", project_name="proj-a", is_default=True)
        other_row = UserProject(id="up2", user_id="user-2", project_name="proj-a", is_default=False)
        mock_session.exec.return_value.all.return_value = [default_row, other_row]

        count = user_project_repository.remove_projects_for_users(mock_session, ["user-1", "user-2"], "proj-a")

        assert count == 2
        assert mock_session.delete.call_count == 2
        mock_session.delete.assert_any_call(default_row)
```

- [ ] **Step 2: Run to verify they pass immediately (no implementation needed)**

Run: `cd /d/Projects/codemie && poetry run pytest tests/codemie/repository/test_user_project_repository_default.py -k TestDefaultClearedOnRemoval -v`
Expected: PASS immediately — this task adds no production code; it exists to make the spec's "automatic, verified" claim an executable, permanent regression check rather than a one-time manual read of the code.

- [ ] **Step 3: Commit**

```bash
git add tests/codemie/repository/test_user_project_repository_default.py
git commit -m "EPMCDME-15110: Add regression proof that removal clears is_default"
```
