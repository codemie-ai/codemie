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

from unittest.mock import MagicMock, patch

import pytest
from fastapi import status
from httpx import ASGITransport, AsyncClient

from codemie.rest_api.main import app
from codemie.rest_api.security.user import User

MANAGEMENT = "codemie.rest_api.routers.assistant.management"


@pytest.fixture(autouse=True)
def override_auth():
    from codemie.rest_api.security.authentication import authenticate

    user = User(id="123", username="testuser", name="Test User")
    app.dependency_overrides[authenticate] = lambda: user
    yield
    app.dependency_overrides = {}


@pytest.mark.asyncio
async def test_create_assistant_saves_disabled_context_tools():
    saved = {}

    def capture(assistant, request, user):
        saved["assistant"] = assistant

    body = {
        "name": "dt-assistant",
        "description": "dt description",
        "slug": "dt-assistant",
        "project": "test-project",
        "system_prompt": "You are a helpful assistant.",
        "llm_model_type": "gpt-4",
        "disabled_context_tools": ["search_kb"],
    }

    with (
        patch(f"{MANAGEMENT}._validate_integrations_or_response", return_value=None),
        patch(f"{MANAGEMENT}.project_access_check"),
        patch(f"{MANAGEMENT}.ensure_application_exists"),
        patch(f"{MANAGEMENT}._save_new_assistant", side_effect=capture),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/v1/assistants", json=body)

    assert response.status_code == status.HTTP_200_OK
    assert saved["assistant"].disabled_context_tools == ["search_kb"]


@pytest.mark.asyncio
async def test_update_assistant_forwards_disabled_context_tools_to_repository():
    assistant = MagicMock()
    assistant.is_global = False
    assistant.context = []

    body = {
        "name": "dt-assistant",
        "project": "test-project",
        "system_prompt": "You are a helpful assistant.",
        "llm_model_type": "gpt-4",
        "disabled_context_tools": ["get_repository_file_tree"],
    }

    with (
        patch(f"{MANAGEMENT}.project_access_check"),
        patch(f"{MANAGEMENT}.get_assistant_by_id_or_raise", return_value=assistant),
        patch(f"{MANAGEMENT}.check_user_can_access_assistant"),
        patch(f"{MANAGEMENT}._validate_remote_entities_and_raise"),
        patch(f"{MANAGEMENT}._update_validation_response", return_value=None),
        patch(f"{MANAGEMENT}.category_service"),
        patch(f"{MANAGEMENT}.GuardrailService"),
        patch(f"{MANAGEMENT}.AssistantRepository") as repository_cls,
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.put("/v1/assistants/assistant-1", json=body)

    assert response.status_code == status.HTTP_200_OK
    forwarded_request = repository_cls.return_value.update.call_args.args[1]
    assert forwarded_request.disabled_context_tools == ["get_repository_file_tree"]
