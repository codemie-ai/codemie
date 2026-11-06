# EPMCDME-14786 — Share an Assistant with Selected Users: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow an assistant owner to grant and revoke USE (READ) access to specific internal CodeMie users, surfacing shared assistants in their visible lists.

**Architecture:** Extend the existing `Permission` table with `ResourceType.ASSISTANT`; write and revoke `Permission` rows from a new `AssistantShareService`; add an OR subquery to `_filter_for_regular_user_visibility` for list-level visibility; add four new endpoints to the assistant router. The generic `/v1/permissions` endpoint is NOT changed — the USER principal path in `PermissionCreationService` stays blocked; all assistant-sharing writes go through the new service layer.

**Tech Stack:** Python, FastAPI, SQLModel, SQLAlchemy, Alembic (`alembic_postgresql_enum`), pytest/MagicMock.

**Spec:** `docs/superpowers/tasks/2026-09-08-epmcdme-14786/spec.md`

Commit per task using the repository's existing convention.

## Global Constraints

- Owner-only enforcement via explicit `assistant.is_owned_by(current_user)` check — stricter than `Ability(user).can(WRITE)` which also passes for managers/admins.
- `permission_level = Action.READ` for all assistant per-user grants; no new `permission_level` values.
- Internal-user filter: `user_type != config.EXTERNAL_USER_TYPE` AND `is_active == True AND deleted_at IS NULL`.
- Alembic migration must be applied before code deployment; uses `alembic_postgresql_enum`'s `sync_enum_values` with `enum_schema='codemie'`.
- No new table — the existing `permissions` table only.
- No changes to the generic `POST /v1/permissions` endpoint or `PermissionCreationService._check_principal`.

---

### Task 1: Extend `ResourceType` enum and `type_for_instance`

**Files:**
- Modify: `src/codemie/rest_api/models/permission.py:29-50`
- Modify: `src/external/alembic/versions/` — new migration file
- Test: `tests/codemie/rest_api/models/test_permission_models.py`

**Interfaces:**
- Produces: `ResourceType.ASSISTANT = "assistant"` enum value usable by later tasks; `type_for_instance(assistant_instance)` returns `ResourceType.ASSISTANT`.

- [ ] **Step 1: Write failing tests**

Add to `tests/codemie/rest_api/models/test_permission_models.py`:

```python
from codemie.rest_api.models.permission import ResourceType
from codemie.rest_api.models.assistant import Assistant

def test_resource_type_assistant_value():
    assert ResourceType.ASSISTANT == "assistant"

def test_type_for_instance_assistant(mocker):
    assistant = mocker.MagicMock(spec=Assistant)
    result = ResourceType.type_for_instance(assistant)
    assert result == ResourceType.ASSISTANT
```

- [ ] **Step 2: Run tests to confirm they fail**

```
pytest tests/codemie/rest_api/models/test_permission_models.py -v -k "assistant"
```

Expected: `AttributeError: ASSISTANT` or `ValueError`.

- [ ] **Step 3: Add `ASSISTANT` to `ResourceType` and update `_lookup`**

In `src/codemie/rest_api/models/permission.py`:

After `DATASOURCE = "datasource"` (L30) add:
```python
    ASSISTANT = "assistant"
```

Replace the `_lookup` property (L48-50) with:
```python
    @classmethod
    @property
    def _lookup(cls):
        from codemie.rest_api.models.assistant import Assistant
        return {cls.DATASOURCE: IndexInfo, cls.ASSISTANT: Assistant}
```

- [ ] **Step 4: Write the Alembic migration**

Create `src/external/alembic/versions/a1b2c3d4e5f8_add_assistant_to_resourcetype.py`:

```python
"""add assistant to resourcetype enum

Revision ID: a1b2c3d4e5f8
Revises: 67b7c43d6090
Create Date: 2026-09-08 00:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
from alembic_postgresql_enum import TableReference

revision: str = 'a1b2c3d4e5f8'
down_revision: Union[str, None] = '67b7c43d6090'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.sync_enum_values(
        enum_schema='codemie',
        enum_name='resourcetype',
        new_values=['DATASOURCE', 'ASSISTANT'],
        affected_columns=[
            TableReference(
                table_schema='codemie',
                table_name='permissions',
                column_name='resource_type',
            )
        ],
        enum_values_to_rename=[],
    )


def downgrade() -> None:
    op.execute("DELETE FROM codemie.permissions WHERE resource_type = 'ASSISTANT'")
    op.sync_enum_values(
        enum_schema='codemie',
        enum_name='resourcetype',
        new_values=['DATASOURCE'],
        affected_columns=[
            TableReference(
                table_schema='codemie',
                table_name='permissions',
                column_name='resource_type',
            )
        ],
        enum_values_to_rename=[],
    )
```

- [ ] **Step 5: Run tests to confirm they pass**

```
pytest tests/codemie/rest_api/models/test_permission_models.py -v
```

---

### Task 2: Extend `AssistantBase.is_shared_with` for per-user grants

**Files:**
- Modify: `src/codemie/rest_api/models/assistant.py:858-868`
- Test: `tests/codemie/core/test_ability.py`

**Interfaces:**
- Consumes: `ResourceType.ASSISTANT` from Task 1; `Permission.get_by_fields(...)`.
- Produces: `is_shared_with(user)` returns `True` when a `Permission` row exists with `resource_type=ASSISTANT`, `resource_id=self.id`, `principal_type=USER`, `principal_id=user.id`.

- [ ] **Step 1: Write failing tests**

Add to `tests/codemie/core/test_ability.py` (follow existing mock patterns):

```python
from unittest.mock import MagicMock, patch
from codemie.core.ability import Ability, Action

def test_is_shared_with_per_user_grant():
    assistant = MagicMock()
    assistant.is_global = False
    assistant.project = "proj"
    assistant.shared = False
    assistant.id = "asst-1"

    user = MagicMock()
    user.is_external_user = False
    user.project_names = []
    user.admin_project_names = []
    user.id = "user-1"

    mock_permission = MagicMock()
    with patch(
        "codemie.rest_api.models.assistant.Permission.get_by_fields",
        return_value=mock_permission,
    ):
        assert assistant.is_shared_with(user) is True


def test_is_shared_with_no_grant_returns_false():
    assistant = MagicMock()
    assistant.is_global = False
    assistant.project = "proj"
    assistant.shared = False
    assistant.id = "asst-1"

    user = MagicMock()
    user.is_external_user = False
    user.project_names = []
    user.admin_project_names = []
    user.id = "user-1"

    with patch(
        "codemie.rest_api.models.assistant.Permission.get_by_fields",
        return_value=None,
    ):
        assert assistant.is_shared_with(user) is False


def test_is_shared_with_project_shared_still_works():
    assistant = MagicMock()
    assistant.is_global = False
    assistant.project = "proj"
    assistant.shared = True
    assistant.id = "asst-1"

    user = MagicMock()
    user.is_external_user = False
    user.project_names = ["proj"]
    user.id = "user-1"

    # Should NOT need to query Permission at all
    with patch(
        "codemie.rest_api.models.assistant.Permission.get_by_fields",
    ) as mock_get:
        result = assistant.is_shared_with(user)
    mock_get.assert_not_called()
    assert result is True
```

- [ ] **Step 2: Run tests to confirm they fail**

```
pytest tests/codemie/core/test_ability.py -v -k "per_user_grant or no_grant or project_shared_still"
```

- [ ] **Step 3: Extend `is_shared_with` in `assistant.py:858-868`**

Replace the method body:

```python
def is_shared_with(self, user: User):
    if self.is_global:
        if user.is_external_user:
            from codemie.configs import config
            allowed_projects = list(set(config.EXTERNAL_USER_ALLOWED_PROJECTS + user.project_names))
            return self.project in allowed_projects
        return True
    # Project-wide sharing (original predicate — evaluated first, no DB call)
    if self.project in user.project_names and self.shared:
        return True
    # Per-user grant
    from codemie.rest_api.models.permission import Permission, ResourceType, PrincipalType
    return Permission.get_by_fields({
        "resource_type": ResourceType.ASSISTANT,
        "resource_id": self.id,
        "principal_type": PrincipalType.USER,
        "principal_id": user.id,
    }) is not None
```

- [ ] **Step 4: Run tests**

```
pytest tests/codemie/core/test_ability.py -v
```

---

### Task 3: Extend SQL visibility filter in `AssistantRepository`

**Files:**
- Modify: `src/codemie/service/assistant/assistant_repository.py:579-614` (`_get_non_global_base_condition`) and `662-695` (`_filter_for_regular_user_visibility`)
- Test: `tests/codemie/service/assistant/test_assistant_repository.py`

**Interfaces:**
- Consumes: `ResourceType.ASSISTANT`, `PrincipalType.USER` from Task 1.
- Produces: both `_get_non_global_base_condition` and `_filter_for_regular_user_visibility` include an `OR assistant.id IN (SELECT resource_id FROM permissions WHERE ...)` clause for the requesting user.

- [ ] **Step 1: Write failing tests**

Add to `tests/codemie/service/assistant/test_assistant_repository.py`:

```python
from unittest.mock import MagicMock, patch

def test_filter_includes_per_user_grant_path(mock_session):
    """User with no project membership sees assistant via Permission row."""
    from codemie.service.assistant.assistant_repository import AssistantRepository, AssistantScope

    user = MagicMock()
    user.is_admin = False
    user.project_names = []
    user.admin_project_names = []
    user.id = "user-99"

    repo = AssistantRepository()
    # Call query; capture the SQL WHERE clause text
    # The test verifies the generated SQL contains a permissions subquery
    with patch.object(mock_session, "exec") as mock_exec:
        mock_exec.return_value.all.return_value = []
        repo.query(scope=AssistantScope.VISIBLE_TO_USER, user=user, session=mock_session)

    call_args = mock_exec.call_args
    sql_str = str(call_args[0][0])
    assert "permissions" in sql_str.lower()
    assert "resource_type" in sql_str.lower()


def test_admin_path_unchanged(mock_session):
    """Admin sees all — permissions subquery not needed."""
    from codemie.service.assistant.assistant_repository import AssistantRepository, AssistantScope

    user = MagicMock()
    user.is_admin = True
    user.project_names = []
    user.admin_project_names = []
    user.id = "admin-1"

    repo = AssistantRepository()
    with patch.object(mock_session, "exec") as mock_exec:
        mock_exec.return_value.all.return_value = []
        repo.query(scope=AssistantScope.VISIBLE_TO_USER, user=user, session=mock_session)
    # Admin path: no per-user filtering needed — just confirm it runs without error
```

- [ ] **Step 2: Run to confirm failure**

```
pytest tests/codemie/service/assistant/test_assistant_repository.py -v -k "per_user_grant or admin_path_unchanged"
```

- [ ] **Step 3: Add per-user-grant OR clause**

In `src/codemie/service/assistant/assistant_repository.py`, add these imports at the top of the file if not already present:
```python
from sqlalchemy import text
```

In `_get_non_global_base_condition` (L593-600), extend `user_guard_expr`:

```python
from codemie.rest_api.models.permission import Permission
per_user_grant_condition = Assistant.id.in_(
    select(Permission.resource_id).where(
        Permission.resource_type == "assistant",
        Permission.principal_type == "user",
        Permission.principal_id == user.id,
    )
)
user_guard_expr = or_(
    and_(Assistant.project.in_(user.project_names), Assistant.shared == True),  # noqa
    Assistant.project.in_(user.admin_project_names),
    self._created_by_user_condition(user),
    per_user_grant_condition,
)
```

In `_filter_for_regular_user_visibility` (L683-695), extend the `or_` in the non-`include_global` branch the same way:

```python
from codemie.rest_api.models.permission import Permission
per_user_grant_condition = Assistant.id.in_(
    select(Permission.resource_id).where(
        Permission.resource_type == "assistant",
        Permission.principal_type == "user",
        Permission.principal_id == user.id,
    )
)
return query.where(
    and_(
        Assistant.is_global == False,  # noqa
        or_(
            and_(Assistant.project.in_(user.project_names), Assistant.shared),
            Assistant.project.in_(user.admin_project_names),
            self._created_by_user_condition(user),
            per_user_grant_condition,
        ),
    )
)
```

Also extend the `include_global=True` branch's `or_` with `per_user_grant_condition`.

- [ ] **Step 4: Run tests**

```
pytest tests/codemie/service/assistant/test_assistant_repository.py -v
```

---

### Task 4: `AssistantShareService` — grant, revoke, list, search

**Files:**
- Create: `src/codemie/service/assistant/assistant_share_service.py`
- Modify: `src/codemie/service/assistant/__init__.py` (re-export)
- Test: `tests/codemie/service/assistant/test_assistant_share_service.py`

**Interfaces:**
- Consumes: `ResourceType.ASSISTANT`, `PrincipalType.USER` (Task 1); `Permission.get_all_by_fields`, `Permission.get_by_fields`, `Permission.save`, `Permission.delete`; `UserDB` from `user_repository`.
- Produces:
  - `AssistantShareService.grant(owner: User, assistant: Assistant, user_ids: list[str]) -> list[dict]`
  - `AssistantShareService.revoke(owner: User, assistant: Assistant, user_ids: list[str]) -> list[dict]`
  - `AssistantShareService.list_shared_with(assistant_id: str) -> list[dict]`
  - `AssistantShareService.search_sharable_users(query: str) -> list[dict]`
  - All return dicts serialisable as `CodeMieUserDetail` subsets: `{id, username, email, name, picture}`.

- [ ] **Step 1: Write failing tests**

Create `tests/codemie/service/assistant/test_assistant_share_service.py`:

```python
import pytest
from unittest.mock import MagicMock, patch, call
from codemie.service.assistant.assistant_share_service import AssistantShareService
from codemie.core.exceptions import ExtendedHTTPException


def _owner(user_id="owner-1"):
    u = MagicMock()
    u.id = user_id
    u.username = "owner"
    u.name = "Owner"
    return u


def _assistant(owner_id="owner-1"):
    a = MagicMock()
    a.id = "asst-1"
    a.is_owned_by = lambda u: u.id == owner_id
    return a


def test_grant_non_owner_raises_403():
    non_owner = _owner("other")
    asst = _assistant("owner-1")
    with pytest.raises(ExtendedHTTPException) as exc:
        AssistantShareService.grant(non_owner, asst, ["uid-1"])
    assert exc.value.status_code == 403


def test_revoke_non_owner_raises_403():
    non_owner = _owner("other")
    asst = _assistant("owner-1")
    with pytest.raises(ExtendedHTTPException) as exc:
        AssistantShareService.revoke(non_owner, asst, ["uid-1"])
    assert exc.value.status_code == 403


def test_grant_external_user_raises_400():
    owner = _owner()
    asst = _assistant()
    external_db_user = MagicMock()
    external_db_user.user_type = "external"
    external_db_user.is_active = True
    external_db_user.deleted_at = None

    with patch(
        "codemie.service.assistant.assistant_share_service.UserRepository"
    ) as MockRepo:
        instance = MockRepo.return_value
        instance.get_by_id.return_value = external_db_user
        with patch("codemie.service.assistant.assistant_share_service.Session"):
            with pytest.raises(ExtendedHTTPException) as exc:
                AssistantShareService.grant(owner, asst, ["uid-ext"])
    assert exc.value.status_code == 400


def test_grant_creates_permission_rows():
    owner = _owner()
    asst = _assistant()
    internal_user = MagicMock()
    internal_user.id = "uid-1"
    internal_user.user_type = "internal"
    internal_user.is_active = True
    internal_user.deleted_at = None
    internal_user.username = "bob"
    internal_user.email = "bob@example.com"
    internal_user.name = "Bob"
    internal_user.picture = None

    with patch(
        "codemie.service.assistant.assistant_share_service.UserRepository"
    ) as MockRepo, patch(
        "codemie.service.assistant.assistant_share_service.Permission.get_by_fields",
        return_value=None,
    ), patch(
        "codemie.service.assistant.assistant_share_service.Permission.save"
    ) as mock_save, patch(
        "codemie.service.assistant.assistant_share_service.Session"
    ):
        MockRepo.return_value.get_by_id.return_value = internal_user
        result = AssistantShareService.grant(owner, asst, ["uid-1"])

    mock_save.assert_called_once()
    assert result[0]["id"] == "uid-1"


def test_grant_idempotent_on_existing_grant():
    owner = _owner()
    asst = _assistant()
    internal_user = MagicMock()
    internal_user.id = "uid-1"
    internal_user.user_type = "internal"
    internal_user.is_active = True
    internal_user.deleted_at = None

    existing_perm = MagicMock()
    with patch(
        "codemie.service.assistant.assistant_share_service.UserRepository"
    ) as MockRepo, patch(
        "codemie.service.assistant.assistant_share_service.Permission.get_by_fields",
        return_value=existing_perm,
    ), patch(
        "codemie.service.assistant.assistant_share_service.Permission.save"
    ) as mock_save, patch(
        "codemie.service.assistant.assistant_share_service.Session"
    ):
        MockRepo.return_value.get_by_id.return_value = internal_user
        AssistantShareService.grant(owner, asst, ["uid-1"])
    mock_save.assert_not_called()


def test_revoke_no_existing_grant_is_noop():
    owner = _owner()
    asst = _assistant()
    with patch(
        "codemie.service.assistant.assistant_share_service.Permission.get_by_fields",
        return_value=None,
    ), patch(
        "codemie.service.assistant.assistant_share_service.Permission.delete"
    ) as mock_del:
        result = AssistantShareService.revoke(owner, asst, ["uid-nobody"])
    mock_del.assert_not_called()
```

- [ ] **Step 2: Run to confirm failures**

```
pytest tests/codemie/service/assistant/test_assistant_share_service.py -v
```

- [ ] **Step 3: Implement `AssistantShareService`**

Create `src/codemie/service/assistant/assistant_share_service.py`:

```python
from fastapi import status
from sqlmodel import Session

from codemie.configs import config
from codemie.core.exceptions import ExtendedHTTPException
from codemie.core.models import CreatedByUser
from codemie.core.ability import Action
from codemie.repository.user_repository import UserRepository
from codemie.rest_api.models.base import BaseModelWithSQLSupport
from codemie.rest_api.models.permission import Permission, ResourceType, PrincipalType
from codemie.rest_api.security.user import User


class AssistantShareService:
    """Grant and revoke per-user READ access to an assistant."""

    @classmethod
    def grant(cls, owner: User, assistant, user_ids: list[str]) -> list[dict]:
        cls._require_owner(owner, assistant)
        repo = UserRepository()
        with Session(BaseModelWithSQLSupport.get_engine()) as session:
            for uid in user_ids:
                db_user = repo.get_by_id(session, uid)
                if not db_user:
                    raise ExtendedHTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail=f"User {uid} not found",
                    )
                if db_user.user_type == config.EXTERNAL_USER_TYPE or not db_user.is_active or db_user.deleted_at:
                    raise ExtendedHTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"User {uid} is not a sharable internal user",
                    )
                # Idempotent: skip if grant already exists
                existing = Permission.get_by_fields({
                    "resource_type": ResourceType.ASSISTANT,
                    "resource_id": assistant.id,
                    "principal_type": PrincipalType.USER,
                    "principal_id": uid,
                })
                if existing:
                    continue
                perm = Permission(
                    resource_type=ResourceType.ASSISTANT,
                    resource_id=assistant.id,
                    principal_type=PrincipalType.USER,
                    principal_id=uid,
                    permission_level=Action.READ,
                    created_by=CreatedByUser(id=owner.id, username=owner.username, name=owner.name),
                )
                perm.save()
        return cls.list_shared_with(assistant.id)

    @classmethod
    def revoke(cls, owner: User, assistant, user_ids: list[str]) -> list[dict]:
        cls._require_owner(owner, assistant)
        for uid in user_ids:
            perm = Permission.get_by_fields({
                "resource_type": ResourceType.ASSISTANT,
                "resource_id": assistant.id,
                "principal_type": PrincipalType.USER,
                "principal_id": uid,
            })
            if perm:
                perm.delete()
        return cls.list_shared_with(assistant.id)

    @classmethod
    def list_shared_with(cls, assistant_id: str) -> list[dict]:
        perms = Permission.get_all_by_fields({
            "resource_type": ResourceType.ASSISTANT,
            "resource_id": assistant_id,
            "principal_type": PrincipalType.USER,
        }, order_by="date")
        repo = UserRepository()
        result = []
        with Session(BaseModelWithSQLSupport.get_engine()) as session:
            for perm in perms:
                db_user = repo.get_by_id(session, perm.principal_id)
                if db_user:
                    result.append({
                        "id": db_user.id,
                        "username": db_user.username,
                        "email": db_user.email,
                        "name": db_user.name,
                        "picture": getattr(db_user, "picture", None),
                    })
        return sorted(result, key=lambda u: u.get("name") or u["username"])

    @classmethod
    def search_sharable_users(cls, query: str) -> list[dict]:
        import asyncio
        from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

        # Use the async query from UserRepository for ILIKE search;
        # filter by user_type != external afterwards
        repo = UserRepository()

        async def _search():
            # Build an async-compatible session from the sync engine's URL
            sync_engine = BaseModelWithSQLSupport.get_engine()
            async_url = str(sync_engine.url).replace("postgresql://", "postgresql+asyncpg://", 1)
            async_engine = create_async_engine(async_url)
            async with AsyncSession(async_engine) as session:
                return await repo.aquery_active_users(session, search=query)

        users = asyncio.run(_search())
        return [
            {
                "id": u.id,
                "username": u.username,
                "email": u.email,
                "name": u.name,
                "picture": getattr(u, "picture", None),
            }
            for u in users
            if u.user_type != config.EXTERNAL_USER_TYPE
        ]

    @staticmethod
    def _require_owner(owner: User, assistant) -> None:
        if not assistant.is_owned_by(owner):
            raise ExtendedHTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only the assistant owner can share or revoke access",
            )
```

- [ ] **Step 4: Re-export from `__init__.py`**

In `src/codemie/service/assistant/__init__.py`, add:
```python
from .assistant_share_service import AssistantShareService
```

- [ ] **Step 5: Run tests**

```
pytest tests/codemie/service/assistant/test_assistant_share_service.py -v
```

---

### Task 5: Four new API endpoints on the assistant router

**Files:**
- Modify: `src/codemie/rest_api/routers/assistant.py` — add four endpoints and request/response models
- Test: `tests/codemie/rest_api/routers/test_assistant_share.py`

**Interfaces:**
- Consumes: `AssistantShareService` (Task 4); existing `authenticate` dependency; existing `AssistantRepository` for fetching the assistant by ID.
- Produces:
  - `GET /v1/assistants/{assistant_id}/shared-with` → `{"shared_with": [...]}`
  - `POST /v1/assistants/{assistant_id}/share` body `{"user_ids": [...]}` → `{"shared_with": [...]}`
  - `DELETE /v1/assistants/{assistant_id}/share` body `{"user_ids": [...]}` → `{"shared_with": [...]}`
  - `GET /v1/assistants/{assistant_id}/sharable-users?q=` → `{"users": [...]}`

- [ ] **Step 1: Write failing tests**

Create `tests/codemie/rest_api/routers/test_assistant_share.py`:

```python
import pytest
from fastapi.testclient import TestClient
from unittest.mock import MagicMock, patch


@pytest.fixture
def client():
    from codemie.app import app
    return TestClient(app)


def _auth_user(user_id="owner-1"):
    u = MagicMock()
    u.id = user_id
    u.username = "owner"
    u.name = "Owner"
    return u


def test_get_shared_with_returns_list(client):
    with patch(
        "codemie.rest_api.routers.assistant.AssistantShareService.list_shared_with",
        return_value=[{"id": "u1", "username": "alice", "email": "a@b.com", "name": "Alice", "picture": None}],
    ), patch(
        "codemie.rest_api.routers.assistant.authenticate",
        return_value=_auth_user(),
    ), patch(
        "codemie.rest_api.routers.assistant.Assistant.get_by_id",
        return_value=MagicMock(id="asst-1"),
    ):
        resp = client.get("/v1/assistants/asst-1/shared-with")
    assert resp.status_code == 200
    assert "shared_with" in resp.json()


def test_post_share_non_owner_returns_403(client):
    mock_assistant = MagicMock()
    mock_assistant.is_owned_by.return_value = False
    with patch(
        "codemie.rest_api.routers.assistant.authenticate",
        return_value=_auth_user("not-owner"),
    ), patch(
        "codemie.rest_api.routers.assistant.Assistant.get_by_id",
        return_value=mock_assistant,
    ), patch(
        "codemie.rest_api.routers.assistant.AssistantShareService.grant",
        side_effect=__import__("codemie.core.exceptions", fromlist=["ExtendedHTTPException"]).ExtendedHTTPException(status_code=403, detail="forbidden"),
    ):
        resp = client.post("/v1/assistants/asst-1/share", json={"user_ids": ["u1"]})
    assert resp.status_code == 403


def test_delete_share_revokes_access(client):
    with patch(
        "codemie.rest_api.routers.assistant.authenticate",
        return_value=_auth_user(),
    ), patch(
        "codemie.rest_api.routers.assistant.Assistant.get_by_id",
        return_value=MagicMock(id="asst-1"),
    ), patch(
        "codemie.rest_api.routers.assistant.AssistantShareService.revoke",
        return_value=[],
    ):
        resp = client.delete("/v1/assistants/asst-1/share", json={"user_ids": ["u1"]})
    assert resp.status_code == 200
    assert resp.json()["shared_with"] == []


def test_sharable_users_filters_internal_only(client):
    with patch(
        "codemie.rest_api.routers.assistant.authenticate",
        return_value=_auth_user(),
    ), patch(
        "codemie.rest_api.routers.assistant.Assistant.get_by_id",
        return_value=MagicMock(id="asst-1"),
    ), patch(
        "codemie.rest_api.routers.assistant.AssistantShareService.search_sharable_users",
        return_value=[{"id": "u2", "username": "bob", "email": "b@b.com", "name": "Bob", "picture": None}],
    ):
        resp = client.get("/v1/assistants/asst-1/sharable-users?q=bo")
    assert resp.status_code == 200
    data = resp.json()
    assert "users" in data
    assert data["users"][0]["username"] == "bob"
```

- [ ] **Step 2: Run to confirm failures**

```
pytest tests/codemie/rest_api/routers/test_assistant_share.py -v
```

- [ ] **Step 3: Add models and endpoints to the assistant router**

In `src/codemie/rest_api/routers/assistant.py`, add at the imports section:

```python
from codemie.service.assistant.assistant_share_service import AssistantShareService
```

Add request/response models near the other `BaseModel` subclasses (after `CategoriesRequest`):

```python
class ShareUsersRequest(BaseModel):
    user_ids: List[str]

class SharedWithResponse(BaseModel):
    shared_with: list

class SharableUsersResponse(BaseModel):
    users: list
```

Add four endpoints (inside the `router`):

```python
@router.get("/assistants/{assistant_id}/shared-with", response_model=SharedWithResponse)
def get_shared_with(assistant_id: str, user: User = Depends(authenticate)):
    assistant = Assistant.get_by_id(id_=assistant_id)
    return SharedWithResponse(shared_with=AssistantShareService.list_shared_with(assistant_id))


@router.post("/assistants/{assistant_id}/share", response_model=SharedWithResponse)
def share_assistant(assistant_id: str, body: ShareUsersRequest, user: User = Depends(authenticate)):
    assistant = Assistant.get_by_id(id_=assistant_id)
    shared = AssistantShareService.grant(user, assistant, body.user_ids)
    return SharedWithResponse(shared_with=shared)


@router.delete("/assistants/{assistant_id}/share", response_model=SharedWithResponse)
def revoke_assistant_share(assistant_id: str, body: ShareUsersRequest, user: User = Depends(authenticate)):
    assistant = Assistant.get_by_id(id_=assistant_id)
    shared = AssistantShareService.revoke(user, assistant, body.user_ids)
    return SharedWithResponse(shared_with=shared)


@router.get("/assistants/{assistant_id}/sharable-users", response_model=SharableUsersResponse)
def get_sharable_users(
    assistant_id: str,
    q: str = Query(default="", min_length=0),
    user: User = Depends(authenticate),
):
    Assistant.get_by_id(id_=assistant_id)  # 404 if not found
    users = AssistantShareService.search_sharable_users(q)
    return SharableUsersResponse(users=users)
```

- [ ] **Step 4: Run tests**

```
pytest tests/codemie/rest_api/routers/test_assistant_share.py -v
```

---

## Negative-constraint pass

| Constraint | Task that honours it |
|---|---|
| External users must NOT be sharable | Task 4: `search_sharable_users` filters `user_type != EXTERNAL_USER_TYPE`; `grant` rejects external users with 400 |
| Non-owners must NOT share or revoke | Tasks 4 & 5: `_require_owner` raises 403 before any DB write |
| Recipients must NOT see edit controls | No task needed — edit controls gate on `Ability(user).can(WRITE)` which stays False for READ-only grants (no code change required) |
| Generic `/v1/permissions` USER path must NOT be unblocked | Not touched in any task; `PermissionCreationService._check_principal` stub is unchanged |
| Existing project-wide `shared` flag must NOT be modified | No task modifies the `shared` column or `AssistantBase.shared`; Tasks 2/3 add OR clauses only |
| Sharing chats/folders out of scope | No task touches conversations or folders |
| Sub-assistant external-link exposure out of scope | No task touches sub-assistant exposure logic |
| Bulk / distribution-list sharing out of scope | All endpoints take `user_ids: list[str]` — no group-expansion or distribution-list logic added |
| No feature flag | No feature-flag infrastructure added in any task |
| No new datasource permissions for recipients | No task touches datasource permissions; `AssistantShareService` writes only `Permission` rows for the assistant resource |

negative-constraints: all verified above — no task violates any of the spec's Non-Goals or "must not" constraints.
