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
from codemie.rest_api.models.assistant import Assistant, QualityValidationResult
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


@pytest.fixture
def marketplace_assistant_mock():
    assistant_mock = MagicMock()
    assistant_mock.id = "456"
    assistant_mock.name = "Test Assistant"
    assistant_mock.description = "Test Description"
    assistant_mock.system_prompt = "Test Prompt"
    assistant_mock.conversation_starters = ["Starter 1", "Starter 2"]
    assistant_mock.toolkits = []
    assistant_mock.context = []
    assistant_mock.is_global = False
    assistant_mock.assistant_ids = []

    return assistant_mock


@pytest.fixture
def marketplace_publish_env(marketplace_assistant_mock):
    with (
        patch(
            "codemie.rest_api.routers.assistant.Assistant.find_by_id",
            return_value=marketplace_assistant_mock,
        ),
        patch(
            "codemie.core.ability.Ability.can",
            return_value=True,
        ),
        patch(
            "codemie.rest_api.routers.assistant.config.MARKETPLACE_LLM_VALIDATION_ON_PUBLISH_ENABLED",
            True,
        ),
        patch(
            "codemie.rest_api.routers.assistant.category_service.validate_category_ids",
            return_value=None,
        ),
        patch(
            "codemie.service.llm_service.utils.set_llm_context",
        ),
        patch(
            "codemie.rest_api.routers.assistant._track_assistant_management_metric",
        ),
        patch(
            "codemie.rest_api.routers.assistant._index_marketplace_assistant",
        ),
        patch.object(
            marketplace_assistant_mock,
            "update",
        ) as mock_update,
    ):
        yield marketplace_assistant_mock, mock_update


def marketplace_publish_payload(**overrides):
    payload = {
        "categories": ["engineering", "productivity"],
    }
    payload.update(overrides)
    return payload


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
async def test_publish_assistant_to_marketplace_when_quality_validation_accepts(
    marketplace_publish_env,
):
    assistant, mock_update = marketplace_publish_env

    validation_result = QualityValidationResult(
        decision="accept",
        reasoning_comment="Assistant is ready for publication.",
        recommendations=None,
    )

    with patch(
        "codemie.rest_api.routers.assistant.AssistantGeneratorService.validate_assistant_for_publish",
        return_value=validation_result,
    ) as mock_quality_validation:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
        ) as ac:
            response = await ac.post(
                f"/v1/assistants/{assistant.id}/marketplace/publish",
                headers={"Authorization": "Bearer testtoken"},
                json=marketplace_publish_payload(),
            )

    mock_quality_validation.assert_called_once()
    mock_update.assert_called_once_with(refresh=True)

    assert assistant.is_global is True
    assert response.status_code == status.HTTP_200_OK
    assert f"Assistant {assistant.id} published to marketplace successfully" in response.json()["message"]


@pytest.mark.asyncio
async def test_publish_assistant_after_quality_validation_reject_and_user_bypasses(
    marketplace_publish_env,
):
    assistant, mock_update = marketplace_publish_env

    validation_result = QualityValidationResult(
        decision="reject",
        reasoning_comment="Assistant requires improvements before publication.",
        recommendations=None,
    )

    with patch(
        "codemie.rest_api.routers.assistant.AssistantGeneratorService.validate_assistant_for_publish",
        return_value=validation_result,
    ) as mock_quality_validation:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
        ) as ac:
            # First attempt: rejected by quality validation
            rejected_response = await ac.post(
                f"/v1/assistants/{assistant.id}/marketplace/publish",
                headers={"Authorization": "Bearer testtoken"},
                json=marketplace_publish_payload(),
            )

            assert rejected_response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
            assert assistant.is_global is False
            mock_update.assert_not_called()

            # Second attempt: user clicks "Publish Anyway"
            bypass_response = await ac.post(
                f"/v1/assistants/{assistant.id}/marketplace/publish",
                headers={"Authorization": "Bearer testtoken"},
                json=marketplace_publish_payload(
                    ignore_recommendations=True,
                ),
            )

    # Validator must NOT run again for Publish Anyway
    mock_quality_validation.assert_called_once()

    mock_update.assert_called_once_with(refresh=True)
    assert assistant.is_global is True

    assert bypass_response.status_code == status.HTTP_200_OK


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
        patch("codemie.service.assistant.category_service.category_service.validate_category_ids", return_value=None),
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
        patch("codemie.service.assistant.category_service.category_service.validate_category_ids", return_value=None),
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


@pytest.mark.asyncio
async def test_publish_returns_422_when_categories_field_absent():
    """Test that publishing without categories field returns 422."""
    assistant_id = "456"

    assistant_mock = MagicMock()
    assistant_mock.id = assistant_id
    assistant_mock.assistant_ids = []

    with (
        patch("codemie.rest_api.routers.assistant.Assistant.find_by_id", return_value=assistant_mock),
        patch("codemie.core.ability.Ability.can", return_value=True),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as ac:
            response = await ac.post(
                f"/v1/assistants/{assistant_id}/marketplace/publish",
                json={},  # No categories field
                headers={"Authorization": "Bearer testtoken"},
            )

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
        result = response.json()
        assert "categories" in str(result).lower()


@pytest.mark.asyncio
async def test_publish_returns_422_when_categories_is_empty_list():
    """Test that publishing with empty categories list returns 422."""
    assistant_id = "456"

    assistant_mock = MagicMock()
    assistant_mock.id = assistant_id
    assistant_mock.assistant_ids = []

    with (
        patch("codemie.rest_api.routers.assistant.Assistant.find_by_id", return_value=assistant_mock),
        patch("codemie.core.ability.Ability.can", return_value=True),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as ac:
            response = await ac.post(
                f"/v1/assistants/{assistant_id}/marketplace/publish",
                json={"categories": []},
                headers={"Authorization": "Bearer testtoken"},
            )

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
        result = response.json()
        assert "categories" in str(result).lower()


@pytest.mark.asyncio
async def test_publish_returns_400_when_categories_contain_invalid_ids():
    """Test that publishing with invalid category IDs returns 400."""
    assistant_id = "456"
    invalid_category_ids = ["non-existent-1", "non-existent-2"]

    assistant_mock = MagicMock()
    assistant_mock.id = assistant_id
    assistant_mock.assistant_ids = []

    with (
        patch("codemie.rest_api.routers.assistant.Assistant.find_by_id", return_value=assistant_mock),
        patch("codemie.core.ability.Ability.can", return_value=True),
        patch("codemie.repository.category_repository.CategoryRepository.get_by_ids", return_value=[]),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as ac:
            response = await ac.post(
                f"/v1/assistants/{assistant_id}/marketplace/publish",
                json={"categories": invalid_category_ids},
                headers={"Authorization": "Bearer testtoken"},
            )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        result = response.json()
        assert "invalid category" in str(result).lower()
        assert "non-existent-1" in str(result) or "non-existent-2" in str(result)


@pytest.mark.asyncio
async def test_update_marketplace_assistant_requires_categories():
    """Test that updating a marketplace assistant with empty categories fails."""
    assistant_id = "456"

    assistant_mock = MagicMock()
    assistant_mock.id = assistant_id
    assistant_mock.is_global = True  # Published to marketplace
    assistant_mock.mcp_servers = []
    assistant_mock.prompt_variables = []

    with (
        patch("codemie.rest_api.routers.assistant.Assistant.find_by_id", return_value=assistant_mock),
        patch("codemie.core.ability.Ability.can", return_value=True),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as ac:
            response = await ac.put(
                f"/v1/assistants/{assistant_id}",
                json={
                    "name": "Updated Assistant",
                    "description": "Updated description",
                    "system_prompt": "Updated prompt",
                    "llm_model_type": "gpt-4",
                    "type": "codemie",
                    "categories": [],  # Empty categories
                },
                headers={"Authorization": "Bearer testtoken"},
            )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        result = response.json()
        assert "category" in str(result).lower() or "categories" in str(result).lower()
