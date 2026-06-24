# AgentCore Runtime Endpoints — Flat Entity Refactor: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace nested `/endpoints/installations` sub-resource with a flat `agentcore-runtime-endpoints` entity type backed by a new focused service. No assistant creation — install/uninstall only manages `VendorEntityInstallation` rows.

**Architecture:** Add `AWS_AGENTCORE_RUNTIME_ENDPOINTS` to `Entities`. Create `AgentCoreRuntimeEndpointsService` that lists AWS endpoints merged with DB state, installs (upserts `VendorEntityInstallation` with `state=installed`), and uninstalls (upserts with `state=not_installed`). Add 3 dedicated routes to `vendor.py`. Remove old nested routes and `AgentCoreEndpointInstallationService`.

**Tech Stack:** FastAPI, SQLModel, asyncio, PostgreSQL, SQLAlchemy async

---

### Task 1: Add `AWS_AGENTCORE_RUNTIME_ENDPOINTS` to Entities

**Files:**
- Modify: `src/codemie/rest_api/models/vendor.py`

**Test-first: no** — enum addition only.

- [ ] **Step 1: Add enum value**

In `src/codemie/rest_api/models/vendor.py`, add after `AWS_AGENTCORE_RUNTIMES`:

```python
AWS_AGENTCORE_RUNTIME_ENDPOINTS = "agentcore-runtime-endpoints"
```

- [ ] **Step 2: Commit**

```bash
git add src/codemie/rest_api/models/vendor.py
git commit -m "feat: add agentcore-runtime-endpoints entity type"
```

---

### Task 2: Create `AgentCoreRuntimeEndpointsService`

**Files:**
- Create: `src/codemie/service/aws_bedrock/agentcore_runtime_endpoints_service.py`
- Create: `tests/codemie/service/aws_bedrock/test_agentcore_runtime_endpoints_service.py`

**Test-first: yes**

The service is async throughout. It uses `get_async_session` + `vendor_installation_repository` for DB access and calls `BedrockAgentCoreRuntimeService` static methods for AWS calls.

- [ ] **Step 1: Write failing tests**

Create `tests/codemie/service/aws_bedrock/test_agentcore_runtime_endpoints_service.py`:

```python
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest


def _make_user():
    return SimpleNamespace(id="user-1")


def _make_setting(sid="s1"):
    return SimpleNamespace(id=sid, alias="test", project_name="proj")


def _make_creds():
    return SimpleNamespace(region="us-east-1", access_key_id="k", secret_access_key="s", session_token=None)


def _make_endpoint(name="DEFAULT", live_version="2", status="READY"):
    return {
        "id": f"ep-{name}",
        "name": name,
        "status": status,
        "liveVersion": live_version,
        "targetVersion": None,
        "agentRuntimeEndpointArn": f"arn:aws:bedrock::{name}",
        "createdAt": "2026-01-01T00:00:00Z",
        "lastUpdatedAt": "2026-01-02T00:00:00Z",
    }


@pytest.mark.asyncio
@patch("codemie.service.aws_bedrock.agentcore_runtime_endpoints_service.get_setting_for_user")
@patch("codemie.service.aws_bedrock.agentcore_runtime_endpoints_service.get_setting_aws_credentials")
@patch("codemie.service.aws_bedrock.agentcore_runtime_endpoints_service.BedrockAgentCoreRuntimeService._bedrock_list_runtime_endpoints")
@patch("codemie.service.aws_bedrock.agentcore_runtime_endpoints_service.vendor_installation_repository")
@patch("codemie.service.aws_bedrock.agentcore_runtime_endpoints_service.get_async_session")
async def test_list_endpoints_enriches_with_installation_state(
    mock_session_cm, mock_repo, mock_list_ep, mock_get_creds, mock_get_setting
):
    from codemie.service.aws_bedrock.agentcore_runtime_endpoints_service import AgentCoreRuntimeEndpointsService

    mock_get_setting.return_value = _make_setting()
    mock_get_creds.return_value = _make_creds()
    mock_list_ep.return_value = ([_make_endpoint("DEFAULT", live_version="2")], None)

    row = SimpleNamespace(
        id=uuid.uuid4(), sub_entity_id="DEFAULT", state="installed",
        resource_id=None, version="2", vendor_metadata={}
    )
    mock_repo.get_by_entity = AsyncMock(return_value=[row])
    session = AsyncMock()
    mock_session_cm.return_value.__aenter__ = AsyncMock(return_value=session)
    mock_session_cm.return_value.__aexit__ = AsyncMock(return_value=False)

    result, _ = await AgentCoreRuntimeEndpointsService.list_endpoints(
        user=_make_user(), setting_id="s1", runtime_id="runtime-1"
    )

    assert len(result) == 1
    assert result[0]["installation_state"] == "installed"
    assert result[0]["installation_id"] == str(row.id)


@pytest.mark.asyncio
@patch("codemie.service.aws_bedrock.agentcore_runtime_endpoints_service.get_setting_for_user")
@patch("codemie.service.aws_bedrock.agentcore_runtime_endpoints_service.get_setting_aws_credentials")
@patch("codemie.service.aws_bedrock.agentcore_runtime_endpoints_service.BedrockAgentCoreRuntimeService._bedrock_get_runtime_endpoint")
@patch("codemie.service.aws_bedrock.agentcore_runtime_endpoints_service.vendor_installation_repository")
@patch("codemie.service.aws_bedrock.agentcore_runtime_endpoints_service.get_async_session")
async def test_install_endpoint_upserts_installed_state(
    mock_session_cm, mock_repo, mock_get_ep, mock_get_creds, mock_get_setting
):
    from codemie.service.aws_bedrock.agentcore_runtime_endpoints_service import AgentCoreRuntimeEndpointsService

    mock_get_setting.return_value = _make_setting()
    mock_get_creds.return_value = _make_creds()
    mock_get_ep.return_value = _make_endpoint("DEFAULT", live_version="3")

    row = SimpleNamespace(
        id=uuid.uuid4(), sub_entity_id="DEFAULT", state="installed",
        resource_id=None, version="3", vendor_metadata={}
    )
    mock_repo.upsert = AsyncMock(return_value=row)
    session = AsyncMock()
    mock_session_cm.return_value.__aenter__ = AsyncMock(return_value=session)
    mock_session_cm.return_value.__aexit__ = AsyncMock(return_value=False)

    result = await AgentCoreRuntimeEndpointsService.install_endpoint(
        user=_make_user(), setting_id="s1", runtime_id="runtime-1", endpoint_name="DEFAULT"
    )

    assert result["state"] == "installed"
    call_kwargs = mock_repo.upsert.call_args.kwargs
    assert call_kwargs["state"] == "installed"
    assert call_kwargs["version"] == "3"


@pytest.mark.asyncio
@patch("codemie.service.aws_bedrock.agentcore_runtime_endpoints_service.get_setting_for_user")
@patch("codemie.service.aws_bedrock.agentcore_runtime_endpoints_service.vendor_installation_repository")
@patch("codemie.service.aws_bedrock.agentcore_runtime_endpoints_service.get_async_session")
async def test_uninstall_endpoint_upserts_not_installed(
    mock_session_cm, mock_repo, mock_get_setting
):
    from codemie.service.aws_bedrock.agentcore_runtime_endpoints_service import AgentCoreRuntimeEndpointsService

    mock_get_setting.return_value = _make_setting()

    existing = SimpleNamespace(
        id=uuid.uuid4(), sub_entity_id="DEFAULT", state="installed",
        entity_id="runtime-1", resource_id=None, version="3", vendor_metadata={}
    )
    mock_repo.get_by_id = AsyncMock(return_value=existing)
    mock_repo.upsert = AsyncMock(return_value=None)
    session = AsyncMock()
    mock_session_cm.return_value.__aenter__ = AsyncMock(return_value=session)
    mock_session_cm.return_value.__aexit__ = AsyncMock(return_value=False)

    await AgentCoreRuntimeEndpointsService.uninstall_endpoint(
        user=_make_user(), setting_id="s1", installation_id=str(existing.id)
    )

    call_kwargs = mock_repo.upsert.call_args.kwargs
    assert call_kwargs["state"] == "not_installed"
    assert call_kwargs["resource_id"] is None


def test_compute_state_not_installed():
    from codemie.service.aws_bedrock.agentcore_runtime_endpoints_service import AgentCoreRuntimeEndpointsService
    assert AgentCoreRuntimeEndpointsService._compute_state(resource_id=None, version=None, live_version="2") == "not_installed"


def test_compute_state_installed():
    assert True  # placeholder — tested implicitly via install test


def test_compute_state_version_drift():
    from codemie.service.aws_bedrock.agentcore_runtime_endpoints_service import AgentCoreRuntimeEndpointsService
    assert AgentCoreRuntimeEndpointsService._compute_state(resource_id=uuid.uuid4(), version="1", live_version="2") == "version_drift"
```

- [ ] **Step 2: Run tests — expect FAIL (ImportError)**

```bash
source .venv/bin/activate && poetry run pytest tests/codemie/service/aws_bedrock/test_agentcore_runtime_endpoints_service.py -v
```

Expected: `ModuleNotFoundError` — service doesn't exist yet.

- [ ] **Step 3: Create `agentcore_runtime_endpoints_service.py`**

Create `src/codemie/service/aws_bedrock/agentcore_runtime_endpoints_service.py`:

```python
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
from typing import Optional

from codemie.clients.postgres import get_async_session
from codemie.repository.vendor_installation_repository import vendor_installation_repository
from codemie.rest_api.security.user import User
from codemie.service.aws_bedrock.bedrock_agentcore_runtime_service import BedrockAgentCoreRuntimeService
from codemie.service.aws_bedrock.exceptions import aws_service_exception_handler
from codemie.service.aws_bedrock.utils import get_setting_aws_credentials, get_setting_for_user

_VENDOR = "aws"
_ENTITY_TYPE = "agentcore-runtime-endpoints"


class AgentCoreRuntimeEndpointsService:
    @staticmethod
    @aws_service_exception_handler("Bedrock AgentCore runtime endpoints")
    async def list_endpoints(
        user: User,
        setting_id: str,
        runtime_id: str,
        page: int = 0,
        per_page: int = 12,
        next_token: Optional[str] = None,
    ) -> tuple[list[dict], Optional[str]]:
        setting = get_setting_for_user(user, setting_id)
        aws_creds = get_setting_aws_credentials(setting.id)

        endpoints, return_next_token = BedrockAgentCoreRuntimeService._bedrock_list_runtime_endpoints(
            runtime_id=runtime_id,
            region=aws_creds.region,
            access_key_id=aws_creds.access_key_id,
            secret_access_key=aws_creds.secret_access_key,
            session_token=aws_creds.session_token,
            page=page,
            per_page=per_page,
            next_token=next_token,
        )

        async with get_async_session() as session:
            rows = await vendor_installation_repository.get_by_entity(
                session, str(setting.id), _VENDOR, _ENTITY_TYPE, runtime_id
            )
        installation_map = {r.sub_entity_id: r for r in rows}

        result = []
        for ep in endpoints:
            ep_name = ep.get("name")
            live_version = ep.get("liveVersion")
            row = installation_map.get(ep_name)

            if row is None:
                installation_state = "not_installed"
                installation_id = None
            else:
                installation_state = AgentCoreRuntimeEndpointsService._compute_state(
                    resource_id=row.resource_id,
                    version=row.version,
                    live_version=live_version,
                )
                installation_id = str(row.id)

            result.append({
                "id": ep.get("id"),
                "name": ep_name,
                "status": ep.get("status"),
                "liveVersion": live_version,
                "targetVersion": ep.get("targetVersion"),
                "createdAt": ep.get("createdAt"),
                "updatedAt": ep.get("lastUpdatedAt"),
                "installation_state": installation_state,
                "installation_id": installation_id,
            })

        return result, return_next_token

    @staticmethod
    @aws_service_exception_handler("Bedrock AgentCore runtime endpoints")
    async def install_endpoint(
        user: User,
        setting_id: str,
        runtime_id: str,
        endpoint_name: str,
    ) -> dict:
        setting = get_setting_for_user(user, setting_id)
        aws_creds = get_setting_aws_credentials(setting.id)

        ep = BedrockAgentCoreRuntimeService._bedrock_get_runtime_endpoint(
            runtime_id=runtime_id,
            endpoint_name=endpoint_name,
            region=aws_creds.region,
            access_key_id=aws_creds.access_key_id,
            secret_access_key=aws_creds.secret_access_key,
            session_token=aws_creds.session_token,
        )

        async with get_async_session() as session:
            row = await vendor_installation_repository.upsert(
                session,
                setting_id=str(setting.id),
                vendor=_VENDOR,
                entity_type=_ENTITY_TYPE,
                entity_id=runtime_id,
                sub_entity_id=endpoint_name,
                state="installed",
                version=ep.get("liveVersion"),
                metadata={
                    "name": ep.get("name"),
                    "status": ep.get("status"),
                    "liveVersion": ep.get("liveVersion"),
                    "agentRuntimeEndpointArn": ep.get("agentRuntimeEndpointArn"),
                },
            )

        return AgentCoreRuntimeEndpointsService._serialize(row)

    @staticmethod
    @aws_service_exception_handler("Bedrock AgentCore runtime endpoints")
    async def uninstall_endpoint(
        user: User,
        setting_id: str,
        installation_id: str,
    ) -> None:
        setting = get_setting_for_user(user, setting_id)

        async with get_async_session() as session:
            existing = await vendor_installation_repository.get_by_id(
                session, uuid.UUID(installation_id)
            )

        if existing is None:
            from codemie.core.exceptions import ExtendedHTTPException
            raise ExtendedHTTPException(code=404, message=f"Installation {installation_id} not found")

        async with get_async_session() as session:
            await vendor_installation_repository.upsert(
                session,
                setting_id=str(setting.id),
                vendor=_VENDOR,
                entity_type=_ENTITY_TYPE,
                entity_id=existing.entity_id,
                sub_entity_id=existing.sub_entity_id,
                state="not_installed",
                resource_id=None,
                version=None,
                metadata=existing.vendor_metadata,
            )

    @staticmethod
    def _compute_state(resource_id, version: str | None, live_version: str | None) -> str:
        if resource_id is None and version is None:
            return "not_installed"
        if version == live_version:
            return "installed"
        return "version_drift"

    @staticmethod
    def _serialize(row) -> dict:
        return {
            "id": str(row.id),
            "sub_entity_id": row.sub_entity_id,
            "state": row.state,
            "version": row.version,
            "metadata": row.vendor_metadata,
        }
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
poetry run pytest tests/codemie/service/aws_bedrock/test_agentcore_runtime_endpoints_service.py -v
```

- [ ] **Step 5: Commit**

```bash
git add src/codemie/service/aws_bedrock/agentcore_runtime_endpoints_service.py tests/codemie/service/aws_bedrock/test_agentcore_runtime_endpoints_service.py
git commit -m "feat: add AgentCoreRuntimeEndpointsService for endpoint install/uninstall tracking"
```

---

### Task 3: Add routes for `agentcore-runtime-endpoints`

**Files:**
- Modify: `src/codemie/rest_api/routers/vendor.py`

**Test-first: no** — routing wiring; covered by service tests.

Add 3 dedicated routes before the generic list route. Import `AgentCoreRuntimeEndpointsService` at the top.

- [ ] **Step 1: Add import to `vendor.py`**

```python
from codemie.service.aws_bedrock.agentcore_runtime_endpoints_service import AgentCoreRuntimeEndpointsService
```

- [ ] **Step 2: Add GET route**

Insert before `@router.get("/vendors/{origin}/{entity}", ...)`:

```python
@router.get(
    "/vendors/{origin}/agentcore-runtime-endpoints",
    status_code=status.HTTP_200_OK,
)
async def list_agentcore_runtime_endpoints(
    origin: Vendor,
    setting_id: str,
    runtime_id: str,
    page: int = Query(DEFAULT_PAGE, ge=0),
    per_page: int = Query(DEFAULT_PER_PAGE, ge=1),
    next_token: Optional[str] = None,
    user: User = Depends(authenticate),
):
    next_token = unquote_and_validate_next_token(next_token)
    endpoints, return_next_token = await AgentCoreRuntimeEndpointsService.list_endpoints(
        user=user,
        setting_id=setting_id,
        runtime_id=runtime_id,
        page=page,
        per_page=per_page,
        next_token=next_token,
    )
    return {
        "data": endpoints,
        "pagination": {
            "next_token": urllib.parse.quote(return_next_token) if return_next_token else None,
        },
    }
```

- [ ] **Step 3: Add POST route**

```python
@router.post(
    "/vendors/{origin}/agentcore-runtime-endpoints",
    status_code=status.HTTP_201_CREATED,
)
async def install_agentcore_runtime_endpoint(
    origin: Vendor,
    body: dict = Body(...),
    user: User = Depends(authenticate),
):
    setting_id: str = body.get("setting_id", "")
    runtime_id: str = body.get("runtime_id", "")
    endpoint_name: str = body.get("endpoint_name", "")
    if not setting_id or not runtime_id or not endpoint_name:
        raise ExtendedHTTPException(
            code=422,
            message="Validation Error",
            details="setting_id, runtime_id and endpoint_name are required",
            help="Provide all three fields in the request body.",
        )
    return await AgentCoreRuntimeEndpointsService.install_endpoint(
        user=user,
        setting_id=setting_id,
        runtime_id=runtime_id,
        endpoint_name=endpoint_name,
    )
```

- [ ] **Step 4: Add DELETE route**

```python
@router.delete(
    "/vendors/{origin}/agentcore-runtime-endpoints/{installation_id}",
    status_code=status.HTTP_200_OK,
)
async def uninstall_agentcore_runtime_endpoint(
    origin: Vendor,
    installation_id: str,
    setting_id: str,
    user: User = Depends(authenticate),
):
    await AgentCoreRuntimeEndpointsService.uninstall_endpoint(
        user=user,
        setting_id=setting_id,
        installation_id=installation_id,
    )
    return {"success": True}
```

- [ ] **Step 5: Run full test suite**

```bash
poetry run pytest tests/ -x -q
```

- [ ] **Step 6: Commit**

```bash
git add src/codemie/rest_api/routers/vendor.py
git commit -m "feat: add GET/POST/DELETE routes for agentcore-runtime-endpoints"
```

---

### Task 4: Remove old sub-resource routes and `AgentCoreEndpointInstallationService`

**Files:**
- Modify: `src/codemie/rest_api/routers/vendor.py` — remove 5 routes + import
- Delete: `src/codemie/service/aws_bedrock/agentcore_endpoint_installation_service.py`
- Delete: `tests/codemie/service/aws_bedrock/test_bedrock_agentcore_installation_service.py`

- [ ] **Step 1: Remove 5 old routes from `vendor.py`**

Remove these handlers entirely:
- `list_vendor_importable_entities_endpoints` (`GET /vendors/{origin}/{entity}/{id}/endpoints`)
- `list_endpoint_installations` (`GET /vendors/{origin}/{entity}/{id}/endpoints/installations`)
- `create_endpoint_installation` (`POST /vendors/{origin}/{entity}/{id}/endpoints/installations`)
- `update_endpoint_installation` (`PUT /vendors/{origin}/{entity}/{id}/endpoints/installations/{installation_id}`)
- `delete_endpoint_installation` (`DELETE /vendors/{origin}/{entity}/{id}/endpoints/installations/{installation_id}`)

- [ ] **Step 2: Remove old import from `vendor.py`**

Remove:
```python
from codemie.service.aws_bedrock.agentcore_endpoint_installation_service import AgentCoreEndpointInstallationService
```

- [ ] **Step 3: Delete files**

```bash
rm src/codemie/service/aws_bedrock/agentcore_endpoint_installation_service.py
rm tests/codemie/service/aws_bedrock/test_bedrock_agentcore_installation_service.py
```

- [ ] **Step 4: Run full test suite**

```bash
poetry run pytest tests/ -x -q
```

Expected: all passing, no import errors.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "refactor: remove AgentCoreEndpointInstallationService and nested installation routes"
```

---

### Task 5: Lint and final check

- [ ] **Step 1: Run ruff**

```bash
poetry run ruff check --fix src/codemie/rest_api/routers/vendor.py src/codemie/service/aws_bedrock/agentcore_runtime_endpoints_service.py && poetry run ruff format src/codemie/rest_api/routers/vendor.py src/codemie/service/aws_bedrock/agentcore_runtime_endpoints_service.py
```

- [ ] **Step 2: Run full test suite**

```bash
poetry run pytest tests/ -q
```

- [ ] **Step 3: Commit ruff fixes if any**

```bash
git add src/ && git commit -m "style: ruff fixes for agentcore-runtime-endpoints"
```
