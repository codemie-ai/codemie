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

from datetime import datetime
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from httpx import AsyncClient, ASGITransport

from codemie.core.constants import BackgroundTaskStatus
from codemie.core.exceptions import ExtendedHTTPException
from codemie.core.models import UserEntity, BackgroundTaskEntity
from codemie.rest_api.main import extended_http_exception_handler
from codemie.rest_api.routers.background_tasks import router
from codemie.rest_api.security.authentication import User

# Create a FastAPI app and include the router
app = FastAPI()
app.include_router(router)
app.add_exception_handler(ExtendedHTTPException, extended_http_exception_handler)
# Mock data
mock_user = UserEntity(
    user_id="user123",
    username="testuser",
    name="test",
)

mock_task = BackgroundTaskEntity(
    id="task123",
    task="Sample Task",
    user=mock_user,
    final_output="Output",
    current_step="Step 1",
    status=BackgroundTaskStatus.STARTED,
    date=datetime.now(),
    update_date=datetime.now(),
)


@pytest.fixture
def anyio_backend():
    return 'asyncio'


@pytest.mark.anyio
@patch("codemie.service.background_tasks_service.BackgroundTasksService.get_task")
@patch("codemie.rest_api.security.authentication.authenticate")
async def test_get_task(mock_authenticate, mock_get_task):
    mock_authenticate.return_value = User(id="user123", username="testuser", name="test")
    mock_get_task.return_value = mock_task

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.get("/v1/tasks/task123", headers={"user-id": "test"})

    assert response.status_code == 200
    assert response.json() == {
        "id": "task123",
        "task": "Sample Task",
        "user": {"user_id": "user123", "username": "testuser", "name": "test"},
        "final_output": "Output",
        "current_step": "Step 1",
        "status": BackgroundTaskStatus.STARTED.value,
        "date": mock_task.date.isoformat(),
        "update_date": mock_task.update_date.isoformat(),
    }


@pytest.mark.anyio
@patch("codemie.service.background_tasks_service.BackgroundTasksService.get_task")
@patch("codemie.rest_api.security.authentication.authenticate")
async def test_get_task_not_found(mock_authenticate, mock_get_task):
    mock_authenticate.return_value = User(id="user123", username="testuser")
    # Mock the service to raise a KeyError when task is not found
    mock_get_task.side_effect = KeyError("No background_tasks found with id 123")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.get("/v1/tasks/123", headers={"user-id": "test"})

    assert response.status_code == 404
    # Verify the response contains the expected error details
    response_json = response.json()
    assert response_json["error"]["message"] == "Task not found"
    assert "123" in response_json["error"]["details"]
    assert "Please verify the task ID" in response_json["error"]["help"]


@pytest.mark.anyio
@patch("codemie.rest_api.security.authentication.authenticate")
async def test_get_task_unauthorized(mock_authenticate):
    mock_authenticate.return_value = None

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.get("/v1/tasks/123")

    assert response.status_code == 401
