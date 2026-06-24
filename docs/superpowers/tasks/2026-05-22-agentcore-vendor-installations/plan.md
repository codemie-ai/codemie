# AgentCore Vendor Installations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Postgres-backed `vendor_entity_installation` table and CRUD API so the frontend can track which AgentCore runtime endpoints have been "installed" as CodeMie resources, with cached AWS metadata and drift detection.

**Architecture:** New `VendorEntityInstallation` SQLModel + `VendorInstallationRepository` (async, upsert pattern) feed four new routes under `/v1/vendors/aws/agentcore-runtimes/{runtimeId}/endpoints/installations`. The GET route auto-registers missing AWS endpoints as `not_installed` rows on every call. Service methods on `BedrockAgentCoreRuntimeService` coordinate access control, AWS fetching, and state computation.

**Tech Stack:** Python 3.12, FastAPI, SQLModel, async SQLAlchemy, PostgreSQL JSONB, Alembic, boto3 `bedrock-agentcore-control`, pytest + AsyncMock

---

## File Structure

| Action | Path | Responsibility |
|--------|------|---------------|
| Create | `src/codemie/repository/vendor_installation_models.py` | `VendorEntityInstallation` SQLModel table definition |
| Create | `src/codemie/repository/vendor_installation_repository.py` | `VendorInstallationRepository` async CRUD |
| Create | `src/external/alembic/versions/<rev>_add_vendor_entity_installation_table.py` | Alembic migration |
| Modify | `src/codemie/service/aws_bedrock/bedrock_agentcore_runtime_service.py` | Add installation service methods; rename `list_importable_entities_for_main_entity` → `list_installable_entities` |
| Modify | `src/codemie/rest_api/routers/vendor.py` | Add 4 installation routes; update call-site for renamed method |
| Create | `tests/codemie/repository/test_vendor_installation_repository.py` | Repository unit tests |
| Create | `tests/codemie/service/aws_bedrock/test_bedrock_agentcore_installation_service.py` | Service unit tests |

---

## Task 1: `VendorEntityInstallation` SQLModel

**Files:**
- Create: `src/codemie/repository/vendor_installation_models.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/codemie/repository/test_vendor_installation_repository.py
from __future__ import annotations

import uuid
from codemie.repository.vendor_installation_models import VendorEntityInstallation


def test_vendor_entity_installation_fields():
    install = VendorEntityInstallation(
        id=uuid.uuid4(),
        setting_id="setting-1",
        vendor="aws",
        entity_type="agentcore-runtimes",
        entity_id="runtime-1",
        sub_entity_id="DEFAULT",
        install_state="not_installed",
        metadata={},
    )
    assert install.install_state == "not_installed"
    assert install.installed_resource_id is None
    assert install.installed_version is None
```

- [ ] **Step 2: Run to verify it fails**

```bash
source .venv/bin/activate
poetry run pytest tests/codemie/repository/test_vendor_installation_repository.py::test_vendor_entity_installation_fields -v
```

Expected: `ModuleNotFoundError` or `ImportError` — file does not exist yet.

- [ ] **Step 3: Create the model**

```python
# src/codemie/repository/vendor_installation_models.py
# Copyright 2026 EPAM Systems, Inc. ("EPAM")
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

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Column, Index, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.sql import func
from sqlmodel import Field, SQLModel


class VendorEntityInstallation(SQLModel, table=True):
    __tablename__ = "vendor_entity_installation"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    setting_id: str = Field(nullable=False, max_length=36)
    vendor: str = Field(nullable=False, max_length=64)
    entity_type: str = Field(nullable=False, max_length=64)
    entity_id: str = Field(nullable=False, max_length=255)
    sub_entity_id: str = Field(nullable=False, max_length=255)
    install_state: str = Field(nullable=False, max_length=32, default="not_installed")
    installed_resource_id: Optional[uuid.UUID] = Field(default=None, nullable=True)
    installed_version: Optional[str] = Field(default=None, nullable=True, max_length=64)
    metadata: Optional[dict] = Field(
        default=None,
        sa_column=Column("metadata", JSONB, nullable=True),
    )
    created_at: Optional[datetime] = Field(
        sa_column=Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now()),
        default=None,
    )
    updated_at: Optional[datetime] = Field(
        sa_column=Column(TIMESTAMP(timezone=True), nullable=True, onupdate=func.now()),
        default=None,
    )

    __table_args__ = (
        UniqueConstraint(
            "setting_id", "vendor", "entity_type", "entity_id", "sub_entity_id",
            name="uq_vendor_entity_installation",
        ),
        Index("ix_vendor_entity_installation_entity", "setting_id", "vendor", "entity_type", "entity_id"),
        {"schema": "codemie"},
    )
```

- [ ] **Step 4: Run to verify it passes**

```bash
poetry run pytest tests/codemie/repository/test_vendor_installation_repository.py::test_vendor_entity_installation_fields -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/codemie/repository/vendor_installation_models.py \
        tests/codemie/repository/test_vendor_installation_repository.py
git commit -m "feat: add VendorEntityInstallation SQLModel"
```

---

## Task 2: Alembic Migration

**Files:**
- Create: `src/external/alembic/versions/<rev>_add_vendor_entity_installation_table.py`

> Generate a new revision ID with `python -c "import uuid; print(uuid.uuid4().hex[:12])"`.
> Set `down_revision` to the current head (check with `poetry run alembic -c src/external/alembic/alembic.ini heads`).

- [ ] **Step 1: Generate revision ID**

```bash
python -c "import uuid; print(uuid.uuid4().hex[:12])"
```

Note the output — use it as `<rev>` throughout this task.

- [ ] **Step 2: Find current head**

```bash
source .venv/bin/activate
poetry run alembic -c src/external/alembic/alembic.ini heads
```

Note the revision hash for `down_revision`.

- [ ] **Step 3: Create migration file**

Replace `<rev>` and `<head_rev>` with the values from steps 1 and 2:

```python
# src/external/alembic/versions/<rev>_add_vendor_entity_installation_table.py
"""add vendor_entity_installation table

Revision ID: <rev>
Revises: <head_rev>
Create Date: 2026-05-22

"""

from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "<rev>"
down_revision: Union[str, None] = "<head_rev>"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "vendor_entity_installation",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("setting_id", sqlmodel.AutoString(length=36), nullable=False),
        sa.Column("vendor", sqlmodel.AutoString(length=64), nullable=False),
        sa.Column("entity_type", sqlmodel.AutoString(length=64), nullable=False),
        sa.Column("entity_id", sqlmodel.AutoString(length=255), nullable=False),
        sa.Column("sub_entity_id", sqlmodel.AutoString(length=255), nullable=False),
        sa.Column("install_state", sqlmodel.AutoString(length=32), nullable=False, server_default="not_installed"),
        sa.Column("installed_resource_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("installed_version", sqlmodel.AutoString(length=64), nullable=True),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "setting_id", "vendor", "entity_type", "entity_id", "sub_entity_id",
            name="uq_vendor_entity_installation",
        ),
        schema="codemie",
    )
    op.create_index(
        "ix_vendor_entity_installation_entity",
        "vendor_entity_installation",
        ["setting_id", "vendor", "entity_type", "entity_id"],
        schema="codemie",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_vendor_entity_installation_entity",
        table_name="vendor_entity_installation",
        schema="codemie",
    )
    op.drop_table("vendor_entity_installation", schema="codemie")
```

- [ ] **Step 4: Verify migration is importable**

```bash
poetry run alembic -c src/external/alembic/alembic.ini check
```

Expected: no import errors (may warn about unapplied migration — that is fine).

- [ ] **Step 5: Commit**

```bash
git add src/external/alembic/versions/<rev>_add_vendor_entity_installation_table.py
git commit -m "feat: add alembic migration for vendor_entity_installation"
```

---

## Task 3: `VendorInstallationRepository`

**Files:**
- Create: `src/codemie/repository/vendor_installation_repository.py`
- Test: `tests/codemie/repository/test_vendor_installation_repository.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/codemie/repository/test_vendor_installation_repository.py`:

```python
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codemie.repository.vendor_installation_models import VendorEntityInstallation
from codemie.repository.vendor_installation_repository import VendorInstallationRepository


@pytest.fixture
def repo() -> VendorInstallationRepository:
    return VendorInstallationRepository()


@pytest.mark.asyncio
async def test_get_by_id_returns_row(repo):
    session = AsyncMock()
    row = SimpleNamespace(id=uuid.uuid4())
    result = MagicMock()
    result.scalars.return_value.first.return_value = row
    session.execute.return_value = result

    found = await repo.get_by_id(session, row.id)

    assert found is row


@pytest.mark.asyncio
async def test_get_by_id_returns_none_when_missing(repo):
    session = AsyncMock()
    result = MagicMock()
    result.scalars.return_value.first.return_value = None
    session.execute.return_value = result

    found = await repo.get_by_id(session, uuid.uuid4())

    assert found is None


@pytest.mark.asyncio
async def test_get_by_entity_returns_list(repo):
    session = AsyncMock()
    rows = [SimpleNamespace(id=uuid.uuid4()), SimpleNamespace(id=uuid.uuid4())]
    result = MagicMock()
    result.scalars.return_value.all.return_value = rows
    session.execute.return_value = result

    found = await repo.get_by_entity(session, "s1", "aws", "agentcore-runtimes", "runtime-1")

    assert found == rows


@pytest.mark.asyncio
async def test_delete_removes_row(repo):
    row = SimpleNamespace(id=uuid.uuid4())
    session = AsyncMock()
    result = MagicMock()
    result.scalars.return_value.first.return_value = row
    session.execute.return_value = result

    await repo.delete(session, row.id)

    session.delete.assert_called_once_with(row)
    session.flush.assert_called_once()


@pytest.mark.asyncio
async def test_delete_noop_when_missing(repo):
    session = AsyncMock()
    result = MagicMock()
    result.scalars.return_value.first.return_value = None
    session.execute.return_value = result

    await repo.delete(session, uuid.uuid4())

    session.delete.assert_not_called()
```

- [ ] **Step 2: Run to verify they fail**

```bash
poetry run pytest tests/codemie/repository/test_vendor_installation_repository.py -v -k "not test_vendor_entity_installation_fields"
```

Expected: `ImportError` — repository does not exist yet.

- [ ] **Step 3: Create the repository**

```python
# src/codemie/repository/vendor_installation_repository.py
# Copyright 2026 EPAM Systems, Inc. ("EPAM")
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

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from codemie.repository.vendor_installation_models import VendorEntityInstallation


class VendorInstallationRepository:

    async def upsert(
        self,
        session: AsyncSession,
        *,
        setting_id: str,
        vendor: str,
        entity_type: str,
        entity_id: str,
        sub_entity_id: str,
        install_state: str,
        installed_resource_id: uuid.UUID | None = None,
        installed_version: str | None = None,
        metadata: dict | None = None,
    ) -> VendorEntityInstallation:
        stmt = (
            pg_insert(VendorEntityInstallation)
            .values(
                id=uuid.uuid4(),
                setting_id=setting_id,
                vendor=vendor,
                entity_type=entity_type,
                entity_id=entity_id,
                sub_entity_id=sub_entity_id,
                install_state=install_state,
                installed_resource_id=installed_resource_id,
                installed_version=installed_version,
                metadata=metadata,
            )
            .on_conflict_do_update(
                constraint="uq_vendor_entity_installation",
                set_={
                    "install_state": pg_insert(VendorEntityInstallation).excluded.install_state,
                    "installed_resource_id": pg_insert(VendorEntityInstallation).excluded.installed_resource_id,
                    "installed_version": pg_insert(VendorEntityInstallation).excluded.installed_version,
                    "metadata": pg_insert(VendorEntityInstallation).excluded.metadata,
                    "updated_at": datetime.now(tz=timezone.utc),
                },
            )
            .returning(VendorEntityInstallation)
        )
        result = await session.execute(stmt)
        await session.flush()
        row = result.scalars().first()
        return row

    async def get_by_entity(
        self,
        session: AsyncSession,
        setting_id: str,
        vendor: str,
        entity_type: str,
        entity_id: str,
    ) -> list[VendorEntityInstallation]:
        stmt = select(VendorEntityInstallation).where(
            VendorEntityInstallation.setting_id == setting_id,
            VendorEntityInstallation.vendor == vendor,
            VendorEntityInstallation.entity_type == entity_type,
            VendorEntityInstallation.entity_id == entity_id,
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())

    async def get_by_id(
        self,
        session: AsyncSession,
        installation_id: uuid.UUID,
    ) -> VendorEntityInstallation | None:
        stmt = select(VendorEntityInstallation).where(
            VendorEntityInstallation.id == installation_id,
        )
        result = await session.execute(stmt)
        return result.scalars().first()

    async def delete(
        self,
        session: AsyncSession,
        installation_id: uuid.UUID,
    ) -> None:
        row = await self.get_by_id(session, installation_id)
        if row is not None:
            await session.delete(row)
            await session.flush()


vendor_installation_repository = VendorInstallationRepository()
```

- [ ] **Step 4: Run to verify all tests pass**

```bash
poetry run pytest tests/codemie/repository/test_vendor_installation_repository.py -v
```

Expected: all 6 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/codemie/repository/vendor_installation_repository.py \
        tests/codemie/repository/test_vendor_installation_repository.py
git commit -m "feat: add VendorInstallationRepository"
```

---

## Task 4: Rename `list_importable_entities_for_main_entity` → `list_installable_entities`

**Files:**
- Modify: `src/codemie/service/aws_bedrock/bedrock_agentcore_runtime_service.py:186`
- Modify: `src/codemie/rest_api/routers/vendor.py:293`

- [ ] **Step 1: Rename in the service**

In `src/codemie/service/aws_bedrock/bedrock_agentcore_runtime_service.py`, rename the method at line 186:

```python
# Old:
def list_importable_entities_for_main_entity(

# New:
def list_installable_entities(
```

- [ ] **Step 2: Update call-site in vendor.py**

In `src/codemie/rest_api/routers/vendor.py`, line 209, 252, and 293, each call reads:
```python
service.list_importable_entities_for_main_entity(
```
Replace all three occurrences with:
```python
service.list_installable_entities(
```

- [ ] **Step 3: Verify no remaining references**

```bash
grep -rn "list_importable_entities_for_main_entity" src/ tests/
```

Expected: no output.

- [ ] **Step 4: Run linter**

```bash
source .venv/bin/activate
poetry run ruff check src/codemie/service/aws_bedrock/bedrock_agentcore_runtime_service.py \
    src/codemie/rest_api/routers/vendor.py
```

Expected: no errors.

- [ ] **Step 5: Commit**

```bash
git add src/codemie/service/aws_bedrock/bedrock_agentcore_runtime_service.py \
        src/codemie/rest_api/routers/vendor.py
git commit -m "refactor: rename list_importable_entities_for_main_entity to list_installable_entities"
```

---

## Task 5: Installation service methods on `BedrockAgentCoreRuntimeService`

**Files:**
- Modify: `src/codemie/service/aws_bedrock/bedrock_agentcore_runtime_service.py`
- Create: `tests/codemie/service/aws_bedrock/test_bedrock_agentcore_installation_service.py`

### Background: `install_state` logic

| State | Condition |
|-------|-----------|
| `not_installed` | `installed_resource_id` is None |
| `installed` | `installed_resource_id` set AND `installed_version == metadata["liveVersion"]` |
| `version_drift` | `installed_resource_id` set AND `installed_version != metadata["liveVersion"]` |
| `deleted_on_aws` | Row exists but endpoint not in AWS list |

- [ ] **Step 1: Write failing tests**

```python
# tests/codemie/service/aws_bedrock/test_bedrock_agentcore_installation_service.py
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codemie.service.aws_bedrock.bedrock_agentcore_runtime_service import BedrockAgentCoreRuntimeService


def _make_user():
    return SimpleNamespace(id="user-1", username="test", name="Test User")


def _make_setting(setting_id="setting-1"):
    return SimpleNamespace(id=setting_id, alias="test-integration", project_name="proj")


def _make_creds():
    return SimpleNamespace(region="us-east-1", access_key_id="AKIA", secret_access_key="secret")


def _make_endpoint(name="DEFAULT", live_version="2", status="READY"):
    return {
        "id": f"ep-{name}",
        "name": name,
        "status": status,
        "liveVersion": live_version,
        "targetVersion": None,
        "agentRuntimeEndpointArn": f"arn:aws:bedrock:us-east-1::endpoint/{name}",
        "agentRuntimeArn": "arn:aws:bedrock:us-east-1::runtime/runtime-1",
        "createdAt": "2026-01-01T00:00:00Z",
        "lastUpdatedAt": "2026-01-02T00:00:00Z",
    }


@pytest.mark.asyncio
@patch("codemie.service.aws_bedrock.bedrock_agentcore_runtime_service.get_setting_for_user")
@patch("codemie.service.aws_bedrock.bedrock_agentcore_runtime_service.get_setting_aws_credentials")
@patch("codemie.service.aws_bedrock.bedrock_agentcore_runtime_service.BedrockAgentCoreRuntimeService._bedrock_list_runtime_endpoints")
@patch("codemie.service.aws_bedrock.bedrock_agentcore_runtime_service.vendor_installation_repository")
@patch("codemie.clients.postgres.get_async_session")
async def test_list_installations_registers_missing_rows(
    mock_session_cm,
    mock_repo,
    mock_list_endpoints,
    mock_get_creds,
    mock_get_setting,
):
    setting = _make_setting()
    creds = _make_creds()
    mock_get_setting.return_value = setting
    mock_get_creds.return_value = creds
    mock_list_endpoints.return_value = ([_make_endpoint("DEFAULT")], None)

    session = AsyncMock()
    mock_session_cm.return_value.__aenter__ = AsyncMock(return_value=session)
    mock_session_cm.return_value.__aexit__ = AsyncMock(return_value=False)

    row = SimpleNamespace(
        id=uuid.uuid4(),
        sub_entity_id="DEFAULT",
        install_state="not_installed",
        installed_resource_id=None,
        installed_version=None,
        metadata={"name": "DEFAULT", "liveVersion": "2"},
    )
    mock_repo.upsert = AsyncMock(return_value=row)
    mock_repo.get_by_entity = AsyncMock(return_value=[row])

    result = await BedrockAgentCoreRuntimeService.list_installations(
        user=_make_user(), setting_id="setting-1", runtime_id="runtime-1", page=0, per_page=10
    )

    assert len(result) == 1
    assert result[0]["sub_entity_id"] == "DEFAULT"
    mock_repo.upsert.assert_called_once()


@pytest.mark.asyncio
@patch("codemie.service.aws_bedrock.bedrock_agentcore_runtime_service.get_setting_for_user")
@patch("codemie.service.aws_bedrock.bedrock_agentcore_runtime_service.get_setting_aws_credentials")
@patch("codemie.service.aws_bedrock.bedrock_agentcore_runtime_service.BedrockAgentCoreRuntimeService._bedrock_get_runtime_endpoint")
@patch("codemie.service.aws_bedrock.bedrock_agentcore_runtime_service.vendor_installation_repository")
@patch("codemie.clients.postgres.get_async_session")
async def test_create_installation_sets_installed_state(
    mock_session_cm,
    mock_repo,
    mock_get_endpoint,
    mock_get_creds,
    mock_get_setting,
):
    setting = _make_setting()
    creds = _make_creds()
    mock_get_setting.return_value = setting
    mock_get_creds.return_value = creds
    mock_get_endpoint.return_value = _make_endpoint("DEFAULT", live_version="3")

    session = AsyncMock()
    mock_session_cm.return_value.__aenter__ = AsyncMock(return_value=session)
    mock_session_cm.return_value.__aexit__ = AsyncMock(return_value=False)

    row = SimpleNamespace(
        id=uuid.uuid4(),
        sub_entity_id="DEFAULT",
        install_state="installed",
        installed_resource_id=None,
        installed_version="3",
        metadata={"liveVersion": "3"},
    )
    mock_repo.upsert = AsyncMock(return_value=row)

    result = await BedrockAgentCoreRuntimeService.create_installation(
        user=_make_user(), setting_id="setting-1", runtime_id="runtime-1", endpoint_name="DEFAULT"
    )

    assert result["install_state"] == "installed"
    call_kwargs = mock_repo.upsert.call_args.kwargs
    assert call_kwargs["install_state"] == "installed"
    assert call_kwargs["installed_version"] == "3"


def test_compute_install_state_not_installed():
    from codemie.service.aws_bedrock.bedrock_agentcore_runtime_service import BedrockAgentCoreRuntimeService
    state = BedrockAgentCoreRuntimeService._compute_install_state(
        installed_resource_id=None, installed_version=None, live_version="2"
    )
    assert state == "not_installed"


def test_compute_install_state_installed():
    from codemie.service.aws_bedrock.bedrock_agentcore_runtime_service import BedrockAgentCoreRuntimeService
    state = BedrockAgentCoreRuntimeService._compute_install_state(
        installed_resource_id=uuid.uuid4(), installed_version="2", live_version="2"
    )
    assert state == "installed"


def test_compute_install_state_version_drift():
    from codemie.service.aws_bedrock.bedrock_agentcore_runtime_service import BedrockAgentCoreRuntimeService
    state = BedrockAgentCoreRuntimeService._compute_install_state(
        installed_resource_id=uuid.uuid4(), installed_version="1", live_version="2"
    )
    assert state == "version_drift"
```

- [ ] **Step 2: Run to verify they fail**

```bash
poetry run pytest tests/codemie/service/aws_bedrock/test_bedrock_agentcore_installation_service.py -v
```

Expected: `ImportError` or `AttributeError` — methods do not exist yet.

- [ ] **Step 3: Add imports and methods to `BedrockAgentCoreRuntimeService`**

Add the following imports at the top of `src/codemie/service/aws_bedrock/bedrock_agentcore_runtime_service.py` (after the existing imports):

```python
import uuid as _uuid_module
from codemie.clients.postgres import get_async_session
from codemie.repository.vendor_installation_repository import vendor_installation_repository
```

Add the following static methods to `BedrockAgentCoreRuntimeService` (before `_bedrock_list_agent_runtimes`):

```python
    @staticmethod
    @aws_service_exception_handler("Bedrock AgentCore runtimes")
    async def list_installations(
        user: User,
        setting_id: str,
        runtime_id: str,
        page: int,
        per_page: int,
        next_token: Optional[str] = None,
    ) -> list[dict]:
        setting: SettingsBase = get_setting_for_user(user, setting_id)
        aws_creds = get_setting_aws_credentials(setting.id)

        endpoints, _ = BedrockAgentCoreRuntimeService._bedrock_list_runtime_endpoints(
            runtime_id=runtime_id,
            region=aws_creds.region,
            access_key_id=aws_creds.access_key_id,
            secret_access_key=aws_creds.secret_access_key,
            page=page,
            per_page=per_page,
            next_token=next_token,
        )

        aws_endpoint_names = {ep.get("name") for ep in endpoints}

        async with get_async_session() as session:
            for ep in endpoints:
                ep_meta = {
                    "name": ep.get("name"),
                    "status": ep.get("status"),
                    "liveVersion": ep.get("liveVersion"),
                    "targetVersion": ep.get("targetVersion"),
                    "agentRuntimeEndpointArn": ep.get("agentRuntimeEndpointArn"),
                    "agentRuntimeArn": ep.get("agentRuntimeArn"),
                    "createdAt": str(ep.get("createdAt")) if ep.get("createdAt") else None,
                    "updatedAt": str(ep.get("lastUpdatedAt")) if ep.get("lastUpdatedAt") else None,
                }
                existing_rows = await vendor_installation_repository.get_by_entity(
                    session, str(setting.id), "aws", "agentcore-runtimes", runtime_id
                )
                existing_sub_ids = {r.sub_entity_id for r in existing_rows}
                ep_name = ep.get("name")
                if ep_name not in existing_sub_ids:
                    await vendor_installation_repository.upsert(
                        session,
                        setting_id=str(setting.id),
                        vendor="aws",
                        entity_type="agentcore-runtimes",
                        entity_id=runtime_id,
                        sub_entity_id=ep_name,
                        install_state="not_installed",
                        metadata=ep_meta,
                    )

            rows = await vendor_installation_repository.get_by_entity(
                session, str(setting.id), "aws", "agentcore-runtimes", runtime_id
            )

        result = []
        for row in rows:
            row_state = row.install_state
            if row.sub_entity_id not in aws_endpoint_names:
                row_state = "deleted_on_aws"
            result.append(BedrockAgentCoreRuntimeService._serialize_installation(row, row_state))

        return result

    @staticmethod
    @aws_service_exception_handler("Bedrock AgentCore runtimes")
    async def create_installation(
        user: User,
        setting_id: str,
        runtime_id: str,
        endpoint_name: str,
    ) -> dict:
        setting: SettingsBase = get_setting_for_user(user, setting_id)
        aws_creds = get_setting_aws_credentials(setting.id)

        ep = BedrockAgentCoreRuntimeService._bedrock_get_runtime_endpoint(
            runtime_id=runtime_id,
            endpoint_name=endpoint_name,
            region=aws_creds.region,
            access_key_id=aws_creds.access_key_id,
            secret_access_key=aws_creds.secret_access_key,
        )

        ep_meta = {
            "name": ep.get("name"),
            "status": ep.get("status"),
            "liveVersion": ep.get("liveVersion"),
            "targetVersion": ep.get("targetVersion"),
            "agentRuntimeEndpointArn": ep.get("agentRuntimeEndpointArn"),
            "agentRuntimeArn": ep.get("agentRuntimeArn"),
            "createdAt": str(ep.get("createdAt")) if ep.get("createdAt") else None,
            "updatedAt": str(ep.get("lastUpdatedAt")) if ep.get("lastUpdatedAt") else None,
        }

        async with get_async_session() as session:
            row = await vendor_installation_repository.upsert(
                session,
                setting_id=str(setting.id),
                vendor="aws",
                entity_type="agentcore-runtimes",
                entity_id=runtime_id,
                sub_entity_id=endpoint_name,
                install_state="installed",
                installed_version=ep.get("liveVersion"),
                metadata=ep_meta,
            )

        return BedrockAgentCoreRuntimeService._serialize_installation(row)

    @staticmethod
    @aws_service_exception_handler("Bedrock AgentCore runtimes")
    async def update_installation(
        user: User,
        setting_id: str,
        runtime_id: str,
        installation_id: str,
    ) -> dict:
        setting: SettingsBase = get_setting_for_user(user, setting_id)
        aws_creds = get_setting_aws_credentials(setting.id)

        async with get_async_session() as session:
            existing = await vendor_installation_repository.get_by_id(
                session, _uuid_module.UUID(installation_id)
            )

        if existing is None:
            from codemie.core.exceptions import ExtendedHTTPException
            raise ExtendedHTTPException(code=404, message=f"Installation {installation_id} not found")

        ep = BedrockAgentCoreRuntimeService._bedrock_get_runtime_endpoint(
            runtime_id=runtime_id,
            endpoint_name=existing.sub_entity_id,
            region=aws_creds.region,
            access_key_id=aws_creds.access_key_id,
            secret_access_key=aws_creds.secret_access_key,
        )

        ep_meta = {
            "name": ep.get("name"),
            "status": ep.get("status"),
            "liveVersion": ep.get("liveVersion"),
            "targetVersion": ep.get("targetVersion"),
            "agentRuntimeEndpointArn": ep.get("agentRuntimeEndpointArn"),
            "agentRuntimeArn": ep.get("agentRuntimeArn"),
            "createdAt": str(ep.get("createdAt")) if ep.get("createdAt") else None,
            "updatedAt": str(ep.get("lastUpdatedAt")) if ep.get("lastUpdatedAt") else None,
        }

        new_state = BedrockAgentCoreRuntimeService._compute_install_state(
            installed_resource_id=existing.installed_resource_id,
            installed_version=existing.installed_version,
            live_version=ep.get("liveVersion"),
        )

        async with get_async_session() as session:
            row = await vendor_installation_repository.upsert(
                session,
                setting_id=str(setting.id),
                vendor="aws",
                entity_type="agentcore-runtimes",
                entity_id=runtime_id,
                sub_entity_id=existing.sub_entity_id,
                install_state=new_state,
                installed_resource_id=existing.installed_resource_id,
                installed_version=existing.installed_version,
                metadata=ep_meta,
            )

        return BedrockAgentCoreRuntimeService._serialize_installation(row)

    @staticmethod
    @aws_service_exception_handler("Bedrock AgentCore runtimes")
    async def delete_installation(
        user: User,
        setting_id: str,
        installation_id: str,
    ) -> None:
        get_setting_for_user(user, setting_id)

        async with get_async_session() as session:
            existing = await vendor_installation_repository.get_by_id(
                session, _uuid_module.UUID(installation_id)
            )

        if existing is None:
            from codemie.core.exceptions import ExtendedHTTPException
            raise ExtendedHTTPException(code=404, message=f"Installation {installation_id} not found")

        async with get_async_session() as session:
            await vendor_installation_repository.delete(session, _uuid_module.UUID(installation_id))

    @staticmethod
    def _compute_install_state(
        installed_resource_id,
        installed_version: Optional[str],
        live_version: Optional[str],
    ) -> str:
        if installed_resource_id is None:
            return "not_installed"
        if installed_version == live_version:
            return "installed"
        return "version_drift"

    @staticmethod
    def _serialize_installation(row, install_state: Optional[str] = None) -> dict:
        return {
            "id": str(row.id),
            "sub_entity_id": row.sub_entity_id,
            "install_state": install_state if install_state is not None else row.install_state,
            "installed_resource_id": str(row.installed_resource_id) if row.installed_resource_id else None,
            "installed_version": row.installed_version,
            "metadata": row.metadata,
        }
```

- [ ] **Step 4: Run tests**

```bash
poetry run pytest tests/codemie/service/aws_bedrock/test_bedrock_agentcore_installation_service.py -v
```

Expected: all tests PASS.

- [ ] **Step 5: Lint**

```bash
poetry run ruff check src/codemie/service/aws_bedrock/bedrock_agentcore_runtime_service.py
```

Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add src/codemie/service/aws_bedrock/bedrock_agentcore_runtime_service.py \
        tests/codemie/service/aws_bedrock/__init__.py \
        tests/codemie/service/aws_bedrock/test_bedrock_agentcore_installation_service.py
git commit -m "feat: add installation CRUD methods to BedrockAgentCoreRuntimeService"
```

> **Note:** Create `tests/codemie/service/aws_bedrock/__init__.py` as an empty file if it does not exist.

---

## Task 6: Vendor Router — Installation Routes

**Files:**
- Modify: `src/codemie/rest_api/routers/vendor.py`

The four new routes must be registered **before** the existing catch-all `GET /vendors/{origin}/{entity}/{vendor_entity_id}/{importable_entity_detail}` route (currently at line 341) so FastAPI resolves them first.

- [ ] **Step 1: Write a smoke test (manual verification)**

The tests for this task are the service-layer tests from Task 5. Route-level verification can be done manually with the browser console snippet after the server starts (Task 7).

- [ ] **Step 2: Add the four routes to `vendor.py`**

Insert the following four route handlers **immediately before** the existing `get_importable_entity_detail` handler (before line 341 `@router.get("/vendors/{origin}/{entity}/{vendor_entity_id}/{importable_entity_detail}"`):

```python
@router.get(
    "/vendors/{origin}/{entity}/{vendor_entity_id}/endpoints/installations",
    status_code=status.HTTP_200_OK,
)
async def list_endpoint_installations(
    origin: Vendor,
    entity: Entities,
    vendor_entity_id: str,
    setting_id: str,
    page: int = Query(DEFAULT_PAGE, ge=0),
    per_page: int = Query(DEFAULT_PER_PAGE, ge=1),
    next_token: Optional[str] = None,
    user: User = Depends(authenticate),
):
    if entity != Entities.AWS_AGENTCORE_RUNTIMES:
        raise ExtendedHTTPException(
            code=404,
            message=NOT_FOUND_MESSAGE,
            details=f"Entity '{entity.value}' does not support installations",
            help="Please provide a valid entity type that supports installations.",
        )
    next_token = unquote_and_validate_next_token(next_token)
    installations = await BedrockAgentCoreRuntimeService.list_installations(
        user=user,
        setting_id=setting_id,
        runtime_id=vendor_entity_id,
        page=page,
        per_page=per_page,
        next_token=next_token,
    )
    return installations


@router.post(
    "/vendors/{origin}/{entity}/{vendor_entity_id}/endpoints/installations",
    status_code=status.HTTP_201_CREATED,
)
async def create_endpoint_installation(
    origin: Vendor,
    entity: Entities,
    vendor_entity_id: str,
    body: dict = Body(...),
    user: User = Depends(authenticate),
):
    if entity != Entities.AWS_AGENTCORE_RUNTIMES:
        raise ExtendedHTTPException(
            code=404,
            message=NOT_FOUND_MESSAGE,
            details=f"Entity '{entity.value}' does not support installations",
            help="Please provide a valid entity type that supports installations.",
        )
    setting_id: str = body.get("setting_id", "")
    endpoint_name: str = body.get("endpoint_name", "")
    if not setting_id or not endpoint_name:
        raise ExtendedHTTPException(
            code=422,
            message="Validation Error",
            details="setting_id and endpoint_name are required",
            help="Provide both fields in the request body.",
        )
    installation = await BedrockAgentCoreRuntimeService.create_installation(
        user=user,
        setting_id=setting_id,
        runtime_id=vendor_entity_id,
        endpoint_name=endpoint_name,
    )
    return installation


@router.put(
    "/vendors/{origin}/{entity}/{vendor_entity_id}/endpoints/installations/{installation_id}",
    status_code=status.HTTP_200_OK,
)
async def update_endpoint_installation(
    origin: Vendor,
    entity: Entities,
    vendor_entity_id: str,
    installation_id: str,
    setting_id: str,
    user: User = Depends(authenticate),
):
    if entity != Entities.AWS_AGENTCORE_RUNTIMES:
        raise ExtendedHTTPException(
            code=404,
            message=NOT_FOUND_MESSAGE,
            details=f"Entity '{entity.value}' does not support installations",
            help="Please provide a valid entity type that supports installations.",
        )
    installation = await BedrockAgentCoreRuntimeService.update_installation(
        user=user,
        setting_id=setting_id,
        runtime_id=vendor_entity_id,
        installation_id=installation_id,
    )
    return installation


@router.delete(
    "/vendors/{origin}/{entity}/{vendor_entity_id}/endpoints/installations/{installation_id}",
    status_code=status.HTTP_200_OK,
)
async def delete_endpoint_installation(
    origin: Vendor,
    entity: Entities,
    vendor_entity_id: str,
    installation_id: str,
    setting_id: str,
    user: User = Depends(authenticate),
):
    if entity != Entities.AWS_AGENTCORE_RUNTIMES:
        raise ExtendedHTTPException(
            code=404,
            message=NOT_FOUND_MESSAGE,
            details=f"Entity '{entity.value}' does not support installations",
            help="Please provide a valid entity type that supports installations.",
        )
    await BedrockAgentCoreRuntimeService.delete_installation(
        user=user,
        setting_id=setting_id,
        installation_id=installation_id,
    )
    return {"success": True}
```

- [ ] **Step 3: Lint**

```bash
poetry run ruff check src/codemie/rest_api/routers/vendor.py
```

Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add src/codemie/rest_api/routers/vendor.py
git commit -m "feat: add /installations routes for agentcore runtime endpoints"
```

---

## Task 7: Integration Smoke Test

**Goal:** Confirm the server starts without import errors and the new routes appear in the OpenAPI schema.

- [ ] **Step 1: Start the server**

```bash
source .venv/bin/activate
cd src && poetry run uvicorn codemie.rest_api.main:app --reload --port 8080
```

Expected: server starts without traceback.

- [ ] **Step 2: Check the new routes appear in OpenAPI**

In a separate terminal:

```bash
curl -s http://localhost:8080/openapi.json | python -m json.tool | grep -A2 "installations"
```

Expected: four routes appear (`GET`, `POST`, `PUT`, `DELETE` under `.../endpoints/installations`).

- [ ] **Step 3: Run the full test suite**

```bash
cd .. && poetry run pytest tests/ -v --tb=short -q
```

Expected: no regressions (all pre-existing tests still pass; new tests pass).

- [ ] **Step 4: Final lint pass**

```bash
poetry run ruff check src/ && poetry run ruff format --check src/
```

Expected: no errors.

- [ ] **Step 5: Commit if any formatting fixes were needed**

```bash
git add -u
git commit -m "chore: ruff format fixes"
```

---

## Spec Coverage Check

| Spec Requirement | Task |
|-----------------|------|
| `vendor_entity_installation` table (all columns) | Task 1 |
| Unique constraint `(setting_id, vendor, entity_type, entity_id, sub_entity_id)` | Task 1 |
| Alembic migration `add_vendor_entity_installation_table` | Task 2 |
| `VendorInstallationRepository` with upsert, get_by_entity, get_by_id, delete | Task 3 |
| Rename `list_importable_entities_for_main_entity` → `list_installable_entities` | Task 4 |
| `list_installations` — fetch AWS, upsert missing rows, return Postgres rows | Task 5 |
| `create_installation` — install_state: installed | Task 5 |
| `update_installation` — resync metadata + recompute state | Task 5 |
| `delete_installation` — remove row | Task 5 |
| `_compute_install_state` — not_installed / installed / version_drift / deleted_on_aws | Task 5 |
| GET `/installations` route (agentcore-guarded) | Task 6 |
| POST `/installations` route | Task 6 |
| PUT `/installations/{id}` route | Task 6 |
| DELETE `/installations/{id}` route | Task 6 |
| 403 via `get_setting_for_user` for bad setting_id access | Tasks 5/6 (decorator) |
| 404 for missing installation record | Task 5 |
| 502 via `aws_service_exception_handler` for AWS failures | Task 5 (decorator) |
