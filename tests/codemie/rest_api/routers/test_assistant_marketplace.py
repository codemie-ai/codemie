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

from unittest.mock import patch, MagicMock

import pytest
from fastapi import status
from httpx import AsyncClient, ASGITransport

from codemie.rest_api.main import app
from codemie.rest_api.models.assistant import Assistant
from codemie.rest_api.security.user import User


@pytest.fixture
def user():
    return User(id="123", username="testuser", name="Test User")


@pytest.fixture
def assistant():
    return Assistant(
        id="456",
        name="Test Assistant",
        description="Test Description",
        system_prompt="Test Prompt",
        toolkits=[],
        is_global=False,
    )


@pytest.fixture(autouse=True)
def override_dependency(user):
    from codemie.rest_api.routers import assistant as assistant_router

    app.dependency_overrides[assistant_router.authenticate] = lambda: user
    yield
    app.dependency_overrides = {}


@pytest.mark.asyncio
async def test_validate_assistant_for_marketplace_no_credentials():
    """Test validation of an assistant for marketplace with no inline credentials."""
    assistant_id = "456"

    assistant_mock = MagicMock()
    assistant_mock.assistant_ids = []

    # Mock validation result with no inline credentials
    validation_result = {"is_valid": True, "inline_credentials": []}

    with (
        patch("codemie.rest_api.routers.assistant.Assistant.find_by_id", return_value=assistant_mock) as mock_find,
        patch("codemie.core.ability.Ability.can", return_value=True),
        patch(
            "codemie.rest_api.routers.assistant._validate_assistant_inline_integrations", return_value=validation_result
        ) as mock_validate,
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.post(
                f"/v1/assistants/{assistant_id}/marketplace/publish/validate",
                headers={"Authorization": "Bearer testtoken"},
            )

        mock_find.assert_called_once_with(assistant_id)
        mock_validate.assert_called_once()
        assert response.status_code == status.HTTP_200_OK
        result = response.json()
        assert not result["requires_confirmation"]
        assert result["assistant_id"] == assistant_id
        assert "is ready to be published" in result["message"]
        assert result["sub_assistants"] == []


@pytest.mark.asyncio
async def test_validate_assistant_for_marketplace_with_subassistants():
    """Test validation of an assistant with sub-assistants for marketplace."""
    assistant_id = "456"

    assistant_mock = MagicMock()
    assistant_mock.assistant_ids = ["sub1", "sub2"]

    # Create sub-assistant mocks
    sub1_mock = MagicMock()
    sub1_mock.id = "sub1"
    sub1_mock.name = "Sub Assistant 1"
    sub1_mock.description = "First sub-assistant"
    sub1_mock.is_global = False

    sub2_mock = MagicMock()
    sub2_mock.id = "sub2"
    sub2_mock.name = "Sub Assistant 2"
    sub2_mock.description = "Second sub-assistant"
    sub2_mock.is_global = True

    def find_by_id_side_effect(id_val):
        if id_val == assistant_id:
            return assistant_mock
        elif id_val == "sub1":
            return sub1_mock
        elif id_val == "sub2":
            return sub2_mock
        return None

    # Mock validation result with no inline credentials
    validation_result = {"is_valid": True, "inline_credentials": []}

    with (
        patch("codemie.rest_api.routers.assistant.Assistant.find_by_id", side_effect=find_by_id_side_effect),
        patch("codemie.core.ability.Ability.can", return_value=True),
        patch(
            "codemie.rest_api.routers.assistant._validate_assistant_inline_integrations", return_value=validation_result
        ),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.post(
                f"/v1/assistants/{assistant_id}/marketplace/publish/validate",
                headers={"Authorization": "Bearer testtoken"},
            )

        assert response.status_code == status.HTTP_200_OK
        result = response.json()
        assert not result["requires_confirmation"]
        assert result["assistant_id"] == assistant_id
        assert "2 sub-assistant(s)" in result["message"]
        assert len(result["sub_assistants"]) == 2
        assert result["sub_assistants"][0]["id"] == "sub1"
        assert result["sub_assistants"][0]["name"] == "Sub Assistant 1"
        assert result["sub_assistants"][0]["is_global"] is False
        assert result["sub_assistants"][1]["id"] == "sub2"
        assert result["sub_assistants"][1]["is_global"] is True


@pytest.mark.asyncio
async def test_validate_assistant_for_marketplace_with_subassistant_credentials():
    """Test validation when sub-assistants have inline credentials."""
    assistant_id = "456"

    assistant_mock = MagicMock()
    assistant_mock.assistant_ids = ["sub1"]

    # Create sub-assistant mock with credentials
    sub1_mock = MagicMock()
    sub1_mock.id = "sub1"
    sub1_mock.name = "Sub Assistant 1"
    sub1_mock.description = "First sub-assistant"
    sub1_mock.is_global = False

    def find_by_id_side_effect(id_val):
        if id_val == assistant_id:
            return assistant_mock
        elif id_val == "sub1":
            return sub1_mock
        return None

    # Mock validation result - main assistant has no credentials
    main_validation_result = {"is_valid": True, "inline_credentials": []}

    # Mock validation result - sub-assistant has credentials
    sub_inline_credentials = [
        {
            "toolkit": "TestToolkit",
            "credential_type": "toolkit_settings",
            "tool": None,
            "label": None,
            "mcp_server": None,
            "env_vars": None,
        }
    ]
    sub_validation_result = {"is_valid": False, "inline_credentials": sub_inline_credentials}

    def validate_integrations_side_effect(asst):
        if asst == assistant_mock:
            return main_validation_result
        elif asst == sub1_mock:
            return sub_validation_result
        return {"is_valid": True, "inline_credentials": []}

    with (
        patch("codemie.rest_api.routers.assistant.Assistant.find_by_id", side_effect=find_by_id_side_effect),
        patch("codemie.core.ability.Ability.can", return_value=True),
        patch(
            "codemie.rest_api.routers.assistant._validate_assistant_inline_integrations",
            side_effect=validate_integrations_side_effect,
        ),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.post(
                f"/v1/assistants/{assistant_id}/marketplace/publish/validate",
                headers={"Authorization": "Bearer testtoken"},
            )

        assert response.status_code == status.HTTP_200_OK
        result = response.json()
        assert result["requires_confirmation"]
        assert result["assistant_id"] == assistant_id
        assert len(result["inline_credentials"]) == 1
        # Check that sub-assistant context is included
        assert result["inline_credentials"][0]["sub_assistant_name"] == "Sub Assistant 1"
        assert result["inline_credentials"][0]["sub_assistant_id"] == "sub1"
        assert "sub-assistants contain inline integration credentials" in result["message"]


@pytest.mark.asyncio
async def test_validate_assistant_for_marketplace_with_credentials():
    """Test validation of an assistant for marketplace with inline credentials."""
    assistant_id = "456"

    assistant_mock = MagicMock()
    assistant_mock.assistant_ids = []

    # Mock inline credentials
    inline_credentials = [
        {
            "toolkit": "TestToolkit",
            "credential_type": "toolkit_settings",
            "tool": None,
            "label": None,
            "mcp_server": None,
            "env_vars": None,
        }
    ]

    # Mock validation result with inline credentials
    validation_result = {
        "is_valid": False,
        "message": "This assistant contains inline integration credentials",
        "inline_credentials": inline_credentials,
    }

    with (
        patch("codemie.rest_api.routers.assistant.Assistant.find_by_id", return_value=assistant_mock) as mock_find,
        patch("codemie.core.ability.Ability.can", return_value=True),
        patch(
            "codemie.rest_api.routers.assistant._validate_assistant_inline_integrations", return_value=validation_result
        ) as mock_validate,
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.post(
                f"/v1/assistants/{assistant_id}/marketplace/publish/validate",
                headers={"Authorization": "Bearer testtoken"},
            )

        mock_find.assert_called_once_with(assistant_id)
        mock_validate.assert_called_once()
        assert response.status_code == status.HTTP_200_OK
        result = response.json()
        assert result["requires_confirmation"]
        assert result["assistant_id"] == assistant_id
        assert len(result["inline_credentials"]) == 1
        assert result["inline_credentials"][0]["toolkit"] == "TestToolkit"
        assert "sub-assistants contain inline integration credentials" in result["message"]


@pytest.mark.asyncio
async def test_validate_assistant_access_denied():
    """Test validation when user does not have access to the assistant."""
    assistant_id = "456"

    with (
        patch("codemie.rest_api.routers.assistant.Assistant.find_by_id", return_value=MagicMock()) as mock_find,
        patch("codemie.core.ability.Ability.can", return_value=False),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.post(
                f"/v1/assistants/{assistant_id}/marketplace/publish/validate",
                headers={"Authorization": "Bearer testtoken"},
            )

        mock_find.assert_called_once_with(assistant_id)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        assert "Access denied" in response.json()["error"]["message"]


@pytest.mark.asyncio
async def test_publish_assistant_to_marketplace():
    """Test publishing an assistant to the marketplace."""
    assistant_id = "456"
    assistant_mock = MagicMock()
    assistant_mock.id = assistant_id  # Set proper id attribute
    assistant_mock.name = "Test Assistant"
    assistant_mock.description = "Test Description"
    assistant_mock.system_prompt = "Test Prompt"
    assistant_mock.conversation_starters = ["Starter 1", "Starter 2"]
    assistant_mock.toolkits = []
    assistant_mock.context = []
    assistant_mock.is_global = False
    assistant_mock.assistant_ids = []  # No sub-assistants

    # Mock quality validation result (accept decision)
    from codemie.rest_api.models.assistant import QualityValidationResult

    quality_validation_mock = QualityValidationResult(
        decision="accept",
        reasoning_comment="Assistant is well-configured and ready for publication.",
        recommendations=None,
    )

    with (
        patch("codemie.rest_api.routers.assistant.Assistant.find_by_id", return_value=assistant_mock) as mock_find,
        patch("codemie.core.ability.Ability.can", return_value=True),
        patch(
            "codemie.rest_api.routers.assistant.AssistantGeneratorService.validate_assistant_for_publish",
            return_value=quality_validation_mock,
        ),
        patch("codemie.service.monitoring.base_monitoring_service.send_log_metric"),
        patch.object(
            assistant_mock, "update", side_effect=lambda *args, **kwargs: setattr(assistant_mock, "is_global", True)
        ) as mock_update,
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.post(
                f"/v1/assistants/{assistant_id}/marketplace/publish",
                headers={"Authorization": "Bearer testtoken"},
                json={"categories": ["engineering", "productivity"]},
            )

        # find_by_id is called twice: once in the endpoint and once in the background indexing task
        assert mock_find.call_count == 2
        mock_find.assert_any_call(assistant_id)
        assert assistant_mock.is_global
        mock_update.assert_called_once_with(refresh=True)
        assert response.status_code == status.HTTP_200_OK
        assert f"Assistant {assistant_id} published to marketplace successfully" in response.json()["message"]


@pytest.mark.asyncio
async def test_publish_assistant_with_subassistants():
    """Test publishing an assistant with sub-assistants to the marketplace (should succeed)."""
    assistant_id = "456"
    assistant_mock = MagicMock()
    assistant_mock.id = assistant_id
    assistant_mock.is_global = False
    assistant_mock.assistant_ids = ["sub1", "sub2"]  # Has sub-assistants

    # Create sub-assistant mocks
    sub1_mock = MagicMock()
    sub1_mock.id = "sub1"
    sub1_mock.is_global = False
    sub1_mock.toolkits = []
    sub1_mock.mcp_servers = []
    sub1_mock.categories = []

    sub2_mock = MagicMock()
    sub2_mock.id = "sub2"
    sub2_mock.is_global = False
    sub2_mock.toolkits = []
    sub2_mock.mcp_servers = []
    sub2_mock.categories = []

    def find_by_id_side_effect(id_val):
        if id_val == assistant_id:
            return assistant_mock
        elif id_val == "sub1":
            return sub1_mock
        elif id_val == "sub2":
            return sub2_mock
        return None

    # Mock quality validation result (accept decision)
    from codemie.rest_api.models.assistant import QualityValidationResult

    quality_validation_mock = QualityValidationResult(
        decision="accept",
        reasoning_comment="Assistant is well-configured and ready for publication.",
        recommendations=None,
    )

    with (
        patch("codemie.rest_api.routers.assistant.Assistant.find_by_id", side_effect=find_by_id_side_effect),
        patch("codemie.core.ability.Ability.can", return_value=True),
        patch.object(assistant_mock, "update") as mock_update_main,
        patch.object(sub1_mock, "update") as mock_update_sub1,
        patch.object(sub2_mock, "update") as mock_update_sub2,
        patch("codemie.rest_api.routers.assistant._track_assistant_management_metric"),
        patch(
            "codemie.rest_api.routers.assistant.AssistantGeneratorService.validate_assistant_for_publish",
            return_value=quality_validation_mock,
        ),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.post(
                f"/v1/assistants/{assistant_id}/marketplace/publish",
                headers={"Authorization": "Bearer testtoken"},
                json={"categories": ["engineering", "productivity"]},
            )

        # Check that sub-assistants were published (is_global set to True)
        assert sub1_mock.is_global
        assert sub2_mock.is_global
        assert assistant_mock.is_global

        # Check all assistants were updated
        mock_update_sub1.assert_called_once_with(refresh=True)
        mock_update_sub2.assert_called_once_with(refresh=True)
        mock_update_main.assert_called_once_with(refresh=True)

        assert response.status_code == status.HTTP_200_OK
        response_data = response.json()
        assert f"Assistant {assistant_id} published to marketplace successfully" in response_data["message"]
        assert "2 sub-assistant(s)" in response_data["message"]


@pytest.mark.asyncio
async def test_publish_assistant_with_subassistants_and_settings():
    """Test publishing an assistant with sub-assistants and custom settings."""
    assistant_id = "456"
    assistant_mock = MagicMock()
    assistant_mock.id = assistant_id
    assistant_mock.is_global = False
    assistant_mock.assistant_ids = ["sub1"]

    # Create sub-assistant mock
    sub1_mock = MagicMock()
    sub1_mock.id = "sub1"
    sub1_mock.is_global = False
    sub1_mock.toolkits = []
    sub1_mock.mcp_servers = []
    sub1_mock.categories = []

    def find_by_id_side_effect(id_val):
        if id_val == assistant_id:
            return assistant_mock
        elif id_val == "sub1":
            return sub1_mock
        return None

    # Mock quality validation result (accept decision)
    from codemie.rest_api.models.assistant import QualityValidationResult

    quality_validation_mock = QualityValidationResult(
        decision="accept",
        reasoning_comment="Assistant is well-configured and ready for publication.",
        recommendations=None,
    )

    with (
        patch("codemie.rest_api.routers.assistant.Assistant.find_by_id", side_effect=find_by_id_side_effect),
        patch("codemie.core.ability.Ability.can", return_value=True),
        patch.object(assistant_mock, "update") as mock_update_main,
        patch.object(sub1_mock, "update") as mock_update_sub1,
        patch("codemie.rest_api.routers.assistant._track_assistant_management_metric"),
        patch(
            "codemie.rest_api.routers.assistant.AssistantGeneratorService.validate_assistant_for_publish",
            return_value=quality_validation_mock,
        ),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.post(
                f"/v1/assistants/{assistant_id}/marketplace/publish",
                headers={"Authorization": "Bearer testtoken"},
                json={
                    "categories": ["engineering"],
                    "sub_assistants_settings": [
                        {
                            "assistant_id": "sub1",
                            "toolkits": [],
                            "mcp_servers": [],
                            "categories": ["productivity"],
                        }
                    ],
                },
            )

        # Check that sub-assistant has the custom categories
        assert sub1_mock.categories == ["productivity"]
        assert sub1_mock.is_global
        assert assistant_mock.is_global
        assert assistant_mock.categories == ["engineering"]

        mock_update_sub1.assert_called_once_with(refresh=True)
        mock_update_main.assert_called_once_with(refresh=True)

        assert response.status_code == status.HTTP_200_OK
        response_data = response.json()
        assert f"Assistant {assistant_id} published to marketplace successfully" in response_data["message"]
        assert "1 sub-assistant(s)" in response_data["message"]


@pytest.mark.asyncio
async def test_unpublish_assistant_from_marketplace():
    """Test unpublishing an assistant from the marketplace."""
    assistant_id = "456"
    assistant_mock = MagicMock()
    assistant_mock.is_global = True

    with (
        patch("codemie.rest_api.routers.assistant.Assistant.find_by_id", return_value=assistant_mock) as mock_find,
        patch("codemie.core.ability.Ability.can", return_value=True),
        patch.object(assistant_mock, "update") as mock_update,
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.post(
                f"/v1/assistants/{assistant_id}/marketplace/unpublish", headers={"Authorization": "Bearer testtoken"}
            )

        mock_find.assert_called_once_with(assistant_id)
        assert not assistant_mock.is_global
        mock_update.assert_called_once_with(refresh=True)
        assert response.status_code == status.HTTP_200_OK
        assert f"Assistant {assistant_id} unpublished from marketplace successfully" in response.json()["message"]


@pytest.mark.asyncio
async def test_unpublish_assistant_access_denied():
    """Test unpublishing when user does not have access to the assistant."""
    assistant_id = "456"

    with (
        patch("codemie.rest_api.routers.assistant.Assistant.find_by_id", return_value=MagicMock()) as mock_find,
        patch("codemie.core.ability.Ability.can", return_value=False),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.post(
                f"/v1/assistants/{assistant_id}/marketplace/unpublish", headers={"Authorization": "Bearer testtoken"}
            )

        mock_find.assert_called_once_with(assistant_id)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        assert "Access denied" in response.json()["error"]["message"]
