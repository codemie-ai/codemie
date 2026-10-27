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

from codemie.configs import config
from tests.codemie.marketplace_fixtures.mounted_app import mounted_marketplace_app
from codemie.rest_api.main import app
from codemie.rest_api.models.assistant import Assistant, QualityValidationResult
from codemie.rest_api.models.assistant_generator import (
    FieldRecommendation,
    RecommendationAction,
    RecommendationSeverity,
    RefineGeneratorResponse,
)
from codemie.rest_api.security.user import User


@pytest.fixture
def non_admin_env(user):
    """Make the request user a plain, non-admin user.

    pytest.ini pins ENV=local, under which every `User` is built as an admin,
    and an armed-gate publish by an admin skips the review queue. Tests asserting
    the queued (non-admin) path request this fixture.
    """
    user.is_admin = False
    user.is_maintainer = False
    yield


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
            "codemie.rest_api.routers.assistant.marketplace.Assistant.find_by_id",
            return_value=marketplace_assistant_mock,
        ),
        patch(
            "codemie.core.ability.Ability.can",
            return_value=True,
        ),
        patch(
            "codemie.rest_api.routers.assistant.marketplace.config.MARKETPLACE_LLM_VALIDATION_ON_PUBLISH_ENABLED",
            True,
        ),
        patch(
            "codemie.rest_api.routers.assistant._sub_assistants.category_service.validate_category_ids",
            return_value=None,
        ),
        patch(
            "codemie.service.llm_service.utils.set_llm_context",
        ),
        patch(
            "codemie.rest_api.routers.assistant.marketplace._track_assistant_management_metric",
        ),
        patch(
            "codemie.rest_api.routers.assistant.marketplace._index_marketplace_assistant",
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
    from codemie.rest_api.security.authentication import authenticate

    app.dependency_overrides[authenticate] = lambda: user
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
        patch(
            "codemie.rest_api.routers.assistant.marketplace.Assistant.find_by_id", return_value=assistant_mock
        ) as mock_find,
        patch("codemie.core.ability.Ability.can", return_value=True),
        patch(
            "codemie.rest_api.routers.assistant.marketplace.validate_assistant_inline_integrations",
            return_value=validation_result,
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
        patch(
            "codemie.rest_api.routers.assistant.marketplace.Assistant.find_by_id", side_effect=find_by_id_side_effect
        ),
        patch("codemie.core.ability.Ability.can", return_value=True),
        patch(
            "codemie.rest_api.routers.assistant.marketplace.validate_assistant_inline_integrations",
            return_value=validation_result,
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
        patch(
            "codemie.rest_api.routers.assistant.marketplace.Assistant.find_by_id", side_effect=find_by_id_side_effect
        ),
        patch("codemie.core.ability.Ability.can", return_value=True),
        patch(
            "codemie.rest_api.routers.assistant.marketplace.validate_assistant_inline_integrations",
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
        patch(
            "codemie.rest_api.routers.assistant.marketplace.Assistant.find_by_id", return_value=assistant_mock
        ) as mock_find,
        patch("codemie.core.ability.Ability.can", return_value=True),
        patch(
            "codemie.rest_api.routers.assistant.marketplace.validate_assistant_inline_integrations",
            return_value=validation_result,
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
        patch(
            "codemie.rest_api.routers.assistant.marketplace.Assistant.find_by_id", return_value=MagicMock()
        ) as mock_find,
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
        "codemie.rest_api.routers.assistant.marketplace.AssistantGeneratorService.validate_assistant_for_publish",
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
@pytest.mark.parametrize("acknowledge, expected_status", [(False, 422), (True, 200)])
async def test_publish_requires_acknowledging_optional_findings_on_accept(
    marketplace_publish_env, acknowledge, expected_status
):
    assistant, _ = marketplace_publish_env
    validation_result = QualityValidationResult(
        decision="accept",
        recommendations=RefineGeneratorResponse(
            fields=[
                FieldRecommendation(
                    name="description",
                    action=RecommendationAction.Change,
                    recommended="Clearer description",
                    reason="Too vague",
                    severity=RecommendationSeverity.OPTIONAL,
                )
            ],
            toolkits=[],
            context=[],
        ),
    )

    with patch(
        "codemie.service.assistant_generator_service.AssistantGeneratorService.validate_assistant_for_publish",
        return_value=validation_result,
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as ac:
            response = await ac.post(
                f"/v1/assistants/{assistant.id}/marketplace/publish",
                headers={"Authorization": "Bearer testtoken"},
                json=marketplace_publish_payload(ignore_recommendations=acknowledge),
            )

    assert response.status_code == expected_status
    if not acknowledge:
        details = response.json()["error"]["details"]
        assert details["requires_confirmation"] is True
        assert details["quality_validation"]["recommendations"]["fields"][0]["name"] == "description"


@pytest.mark.asyncio
async def test_publish_assistant_after_quality_validation_reject_and_user_bypasses(
    marketplace_publish_env,
    user,
):
    # Only an admin/maintainer may waive the advisory findings (F-83). Do not rely
    # on pytest.ini's ENV=local: other tests reassign the process-wide config.ENV.
    user.is_admin = True
    assistant, mock_update = marketplace_publish_env

    validation_result = QualityValidationResult(
        decision="reject",
        reasoning_comment="Assistant requires improvements before publication.",
        recommendations=None,
    )

    with patch(
        "codemie.rest_api.routers.assistant.marketplace.AssistantGeneratorService.validate_assistant_for_publish",
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
        patch(
            "codemie.rest_api.routers.assistant.marketplace.Assistant.find_by_id", side_effect=find_by_id_side_effect
        ),
        patch("codemie.core.ability.Ability.can", return_value=True),
        patch.object(assistant_mock, "update") as mock_update_main,
        patch.object(sub1_mock, "update") as mock_update_sub1,
        patch.object(sub2_mock, "update") as mock_update_sub2,
        patch("codemie.rest_api.routers.assistant.marketplace._track_assistant_management_metric"),
        patch("codemie.rest_api.routers.assistant._sub_assistants._track_assistant_management_metric"),
        patch("codemie.service.assistant.category_service.category_service.validate_category_ids", return_value=None),
        patch(
            "codemie.rest_api.routers.assistant.marketplace.AssistantGeneratorService.validate_assistant_for_publish",
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
        patch(
            "codemie.rest_api.routers.assistant.marketplace.Assistant.find_by_id", side_effect=find_by_id_side_effect
        ),
        patch("codemie.core.ability.Ability.can", return_value=True),
        patch.object(assistant_mock, "update") as mock_update_main,
        patch.object(sub1_mock, "update") as mock_update_sub1,
        patch("codemie.rest_api.routers.assistant.marketplace._track_assistant_management_metric"),
        patch("codemie.rest_api.routers.assistant._sub_assistants._track_assistant_management_metric"),
        patch("codemie.service.assistant.category_service.category_service.validate_category_ids", return_value=None),
        patch(
            "codemie.rest_api.routers.assistant.marketplace.AssistantGeneratorService.validate_assistant_for_publish",
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
    """Unpublish goes through the real WITHDRAW decision, not a bare field assignment."""
    assistant_id = "456"
    assistant_mock = MagicMock()
    assistant_mock.id = assistant_id
    assistant_mock.is_global = True

    mock_mqs_instance = MagicMock()
    mock_mqs_class = MagicMock(return_value=mock_mqs_instance)

    with (
        patch(
            "codemie.rest_api.routers.assistant.marketplace.Assistant.find_by_id", return_value=assistant_mock
        ) as mock_find,
        patch("codemie.core.ability.Ability.can", return_value=True),
        patch(
            "codemie.service.marketplace_management.queue_service.MarketplaceQueueService",
            mock_mqs_class,
        ),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.post(
                f"/v1/assistants/{assistant_id}/marketplace/unpublish", headers={"Authorization": "Bearer testtoken"}
            )

        mock_find.assert_called_once_with(assistant_id)
        mock_mqs_instance.withdraw.assert_called_once()
        assert mock_mqs_instance.withdraw.call_args.kwargs["assistant_id"] == assistant_id
        assert response.status_code == status.HTTP_200_OK
        assert f"Assistant {assistant_id} unpublished from marketplace successfully" in response.json()["message"]


@pytest.mark.asyncio
async def test_unpublish_assistant_access_denied():
    """Test unpublishing when user does not have access to the assistant."""
    assistant_id = "456"

    with (
        patch(
            "codemie.rest_api.routers.assistant.marketplace.Assistant.find_by_id", return_value=MagicMock()
        ) as mock_find,
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
        patch("codemie.rest_api.routers.assistant.marketplace.Assistant.find_by_id", return_value=assistant_mock),
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
        patch("codemie.rest_api.routers.assistant.marketplace.Assistant.find_by_id", return_value=assistant_mock),
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
        patch("codemie.rest_api.routers.assistant.marketplace.Assistant.find_by_id", return_value=assistant_mock),
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
        patch("codemie.rest_api.routers.assistant.management.Assistant.find_by_id", return_value=assistant_mock),
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


# ---------------------------------------------------------------------------
# Publish-time approval gate (EXECUTION-PLAN 0.3, F-01 / F-16)
#
# The gate ships OFF: publishing must keep its current behaviour by default.
# When an operator switches it on, publishing must park the assistant in
# PENDING_REVIEW and leave it private until a moderator approves it.
# ---------------------------------------------------------------------------


def _publish_ready_assistant_mock(assistant_id: str) -> MagicMock:
    """An assistant that is private and has never been published."""
    assistant_mock = MagicMock()
    assistant_mock.id = assistant_id
    assistant_mock.name = "Test Assistant"
    assistant_mock.description = "Test Description"
    assistant_mock.system_prompt = "Test Prompt"
    assistant_mock.conversation_starters = ["Starter 1"]
    assistant_mock.toolkits = []
    assistant_mock.context = []
    assistant_mock.is_global = False
    assistant_mock.marketplace_status = None
    assistant_mock.assistant_ids = []
    return assistant_mock


async def _publish(assistant_mock, gate_enabled: bool):
    """POST the publish endpoint with the approval gate flag set to *gate_enabled*."""
    from codemie.rest_api.models.assistant import QualityValidationResult

    quality_validation_mock = QualityValidationResult(
        decision="accept",
        reasoning_comment="Assistant is well-configured and ready for publication.",
        recommendations=None,
    )

    with (
        patch("codemie.rest_api.models.assistant.Assistant.find_by_id", return_value=assistant_mock),
        patch("codemie.core.ability.Ability.can", return_value=True),
        patch(
            "codemie.service.assistant_generator_service.AssistantGeneratorService.validate_assistant_for_publish",
            return_value=quality_validation_mock,
        ),
        patch("codemie.service.monitoring.base_monitoring_service.send_log_metric"),
        patch("codemie.service.assistant.category_service.category_service.validate_category_ids", return_value=None),
        patch("codemie.rest_api.routers.assistant.marketplace._index_marketplace_assistant"),
        # The gate itself and the SUBMIT transition are exercised end-to-end
        # against a real database in `test_publish_creates_a_pending_review_item`
        # and `test_publish_runs_deterministic_gates.py`. Here they are stubbed
        # so this stays what it was written to be: a characterisation test of
        # the *flag branch*, over a MagicMock assistant and no database.
        patch("codemie.rest_api.routers.assistant.marketplace._enforce_publish_gates", return_value=[]),
        patch(
            "codemie.rest_api.routers.assistant.marketplace._submit_for_review",
            return_value="2026-08-22T09:00:00+00:00",
        ),
        patch.object(config, "MARKETPLACE_APPROVAL_GATE_ENABLED", gate_enabled),
        # The gate is armed only together with marketplace management (`is_approval_gate_armed`).
        patch.object(config, "MARKETPLACE_MANAGEMENT_ENABLED", gate_enabled),
        patch.object(assistant_mock, "update"),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            return await ac.post(
                f"/v1/assistants/{assistant_mock.id}/marketplace/publish",
                headers={"Authorization": "Bearer testtoken"},
                json={"categories": ["engineering", "productivity"]},
            )


@pytest.mark.asyncio
async def test_publish_sets_pending_when_gate_enabled(non_admin_env):
    """Gate on: the assistant is queued for review, not made public."""
    assistant_mock = _publish_ready_assistant_mock("456")

    response = await _publish(assistant_mock, gate_enabled=True)

    assert response.status_code == status.HTTP_200_OK
    assert assistant_mock.marketplace_status == "PENDING_REVIEW"
    assert assistant_mock.is_global is False


@pytest.mark.asyncio
async def test_publish_keeps_current_behaviour_when_gate_disabled():
    """Gate off (the shipping default): publish goes straight live, as today."""
    assistant_mock = _publish_ready_assistant_mock("456")

    response = await _publish(assistant_mock, gate_enabled=False)

    assert response.status_code == status.HTTP_200_OK
    assert assistant_mock.marketplace_status == "APPROVED"
    assert assistant_mock.is_global is True


# ---------------------------------------------------------------------------
# 7.8 — publish with no body is a client error, not a server crash (F-86, F-24)
#
# `request: Optional[PublishToMarketplaceRequest] = None` is dereferenced
# unguarded on the first line of the handler (`request.categories`), while a
# later line guards the same variable — an oversight, not a design. A missing
# body must name the field it is missing.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_publish_with_no_body_returns_422():
    """POST with no JSON body at all: 422 naming `categories`, never a 500."""
    assistant_id = "456"

    assistant_mock = MagicMock()
    assistant_mock.id = assistant_id
    assistant_mock.assistant_ids = []

    with (
        patch("codemie.rest_api.models.assistant.Assistant.find_by_id", return_value=assistant_mock),
        patch("codemie.core.ability.Ability.can", return_value=True),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as ac:
            response = await ac.post(
                f"/v1/assistants/{assistant_id}/marketplace/publish",
                headers={"Authorization": "Bearer testtoken"},
            )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY, response.text
    assert "categories" in response.text.lower()
    assert "Traceback" not in response.text


# ---------------------------------------------------------------------------
# 7.1 — publish becomes a submission (F-01, F-02)
#
# With the gate armed, publishing must park the assistant in the moderation
# queue: PENDING_REVIEW, still private, recorded in `mm_review_decision` as a
# SUBMIT with a SUBMITTED event, visible to the reviewer at GET /pending with
# a real submitter and a real submission time — and NOT indexed into the
# marketplace search index, because an unapproved assistant that is findable
# is a published assistant.
#
# Driven against a real, migrated PostgreSQL: the transition is a
# compare-and-set plus a ledger insert in one transaction, which no mocked
# session can express.
# ---------------------------------------------------------------------------

PENDING_URL = "/v1/marketplace-management/entities/assistants/pending"

_LONG_PROMPT = (
    "You are a release-notes assistant for a platform engineering group. "
    "You summarise merge requests into human readable release notes, you group "
    "them by component, and you always cite the merge request identifier next "
    "to every bullet you produce. When the user asks for a changelog you answer "
    "with markdown headings per component and a short paragraph explaining the "
    "user visible impact of each change. You never invent a merge request that "
    "was not given to you, and when the input is empty you say so plainly "
    "instead of producing an empty document. "
) * 3


@pytest.fixture
def marketplace_router_mounted():
    """The pending queue is read through the marketplace-management router."""
    yield from mounted_marketplace_app()


@pytest.mark.asyncio
async def test_publish_creates_a_pending_review_item(non_admin_env, mm_publish_db, user, marketplace_router_mounted):
    """Gate on: publish enqueues a review item and publishes nothing."""
    from fastapi import Request
    from sqlalchemy import text
    from sqlmodel import Session

    from codemie.core.models import CreatedByUser
    from codemie.rest_api.security.authentication import authenticate

    user.email = "testuser@codemie.ai"

    with mm_publish_db.connect() as conn:
        conn.execute(
            text(
                "insert into codemie.users (id, username, email, name, is_admin, is_active, date) "
                "values (:i, :u, :e, :n, true, true, now()) on conflict (id) do nothing"
            ),
            {"i": user.id, "u": user.username, "e": user.email, "n": user.name},
        )
        conn.commit()

    with Session(mm_publish_db) as session:
        session.add(
            Assistant(
                id="sub-1",
                name="Release Notes Assistant",
                description=(
                    "Summarises merge requests into structured, component-grouped release "
                    "notes with citations back to every source merge request identifier."
                ),
                system_prompt=_LONG_PROMPT,
                categories=["engineering"],
                project="demo",
                creator="someone-else",
                # Deliberately NOT the publisher: `submitted_by` must be the
                # person who put this in front of a reviewer, which is what the
                # SUBMIT decision records. `created_by` is the author, and on a
                # cloned or handed-over assistant the two differ.
                created_by=CreatedByUser(id="someone-else", username="cara", name="Cara Creator"),
                is_global=False,
                marketplace_status="DRAFT",
                shared=True,
            )
        )
        session.commit()

    async def _authenticate(request: Request) -> User:
        request.state.user = user
        return user

    app.dependency_overrides[authenticate] = _authenticate

    with (
        patch("codemie.core.ability.Ability.can", return_value=True),
        patch("codemie.service.assistant.category_service.category_service.validate_category_ids", return_value=None),
        patch("codemie.service.monitoring.base_monitoring_service.send_log_metric"),
        patch("codemie.rest_api.routers.assistant.marketplace._index_marketplace_assistant") as index_spy,
        patch.object(config, "MARKETPLACE_APPROVAL_GATE_ENABLED", True),
        # The gate is armed only together with marketplace management (`is_approval_gate_armed`).
        patch.object(config, "MARKETPLACE_MANAGEMENT_ENABLED", True),
        patch.object(config, "MARKETPLACE_LLM_VALIDATION_ON_PUBLISH_ENABLED", False),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            published = await ac.post(
                "/v1/assistants/sub-1/marketplace/publish",
                headers={"Authorization": "Bearer testtoken"},
                json={"categories": ["engineering"]},
            )
            # The queue is a moderator view: read it as an admin, after the
            # non-admin author has published.
            user.is_admin = True
            queue = await ac.get(PENDING_URL, headers={"Authorization": "Bearer testtoken"})

    assert published.status_code == status.HTTP_200_OK, published.text

    # 1. It is in the queue, with a submitter and a submission time.
    assert queue.status_code == status.HTTP_200_OK, queue.text
    body = queue.json()
    assert body["total"] == 1, body
    row = body["items"][0]
    assert row["assistant_id"] == "sub-1"
    assert row["submitted_by"] == user.name  # the publisher, not "Cara Creator"
    assert row["submitted_at"] is not None

    # 2. It is NOT in the marketplace.
    with mm_publish_db.connect() as conn:
        stored = (
            conn.execute(
                text(
                    "select marketplace_status, is_global, marketplace_submitted_at "
                    "  from codemie.assistants where id = 'sub-1'"
                )
            )
            .mappings()
            .one()
        )
        decision = (
            conn.execute(text("select decision, from_status, to_status, decided_by_id from codemie.mm_review_decision"))
            .mappings()
            .one()
        )
        # The moderation audit trail moved off `mm_marketplace_event` and onto
        # `mm_review_decision` (asserted below) plus the platform-wide
        # `activity_events` feed, which only writes when
        # config.ACTIVITY_EVENTS_ENABLED is on -- it ships off. The decision row
        # is the record that must hold unconditionally, so that is what this
        # test asserts.

    assert stored["marketplace_status"] == "PENDING_REVIEW"
    assert stored["is_global"] is False
    assert stored["marketplace_submitted_at"] is not None

    # 3. It went through the SUBMIT decision path, not a bare field assignment.
    assert decision["decision"] == "SUBMIT"
    assert decision["from_status"] == "DRAFT"
    assert decision["to_status"] == "PENDING_REVIEW"
    assert decision["decided_by_id"] == user.id
    assert decision["decision"] == "SUBMIT"

    # 4. TODO(Phase 7) from step 0.3: an unapproved row must not be indexed.
    index_spy.assert_not_called()


# ---------------------------------------------------------------------------
# EPMCDME-14999: gate-check (dry-run) endpoint
# ---------------------------------------------------------------------------


def _gate_check_assistant_mock(assistant_id: str) -> MagicMock:
    mock = MagicMock()
    mock.id = assistant_id
    mock.name = "Dry Run Assistant"
    mock.description = "A description long enough"
    mock.system_prompt = "A system prompt long enough for gates"
    mock.categories = ["engineering"]
    mock.assistant_ids = []
    return mock


@pytest.mark.asyncio
async def test_gate_check_returns_200_passed_when_no_findings():
    """gate-check returns 200 {passed: true} when all gates pass."""
    assistant_id = "gate-check-1"
    mock = _gate_check_assistant_mock(assistant_id)

    with (
        patch("codemie.rest_api.models.assistant.Assistant.find_by_id", return_value=mock),
        patch("codemie.core.ability.Ability.can", return_value=True),
        patch("codemie.rest_api.routers.assistant.marketplace.is_approval_gate_armed", return_value=True),
        patch(
            "codemie.rest_api.routers.assistant.marketplace.config.MARKETPLACE_LLM_VALIDATION_ON_PUBLISH_ENABLED", False
        ),
        patch(
            "codemie.rest_api.routers.assistant.marketplace._publish_gate_findings",
            return_value=[],
        ),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as ac:
            response = await ac.post(
                f"/v1/assistants/{assistant_id}/marketplace/publish/gate-check",
                json={
                    "name": "My Assistant",
                    "categories": ["engineering"],
                    "system_prompt": "A long enough system prompt for the gate",
                    "description": "A long enough description",
                },
                headers={"Authorization": "Bearer testtoken"},
            )

    assert response.status_code == status.HTTP_200_OK
    result = response.json()
    assert result["passed"] is True
    assert result["gate_findings"] == []


@pytest.mark.asyncio
async def test_gate_check_returns_422_when_critical_findings_present():
    """gate-check returns 422 with the same error structure as publish when gates fail."""
    assistant_id = "gate-check-2"
    mock = _gate_check_assistant_mock(assistant_id)
    findings = [
        {"rule": "system_prompt_length", "severity": "CRITICAL", "message": "Too short", "detail": {"length": 5}},
    ]

    with (
        patch("codemie.rest_api.models.assistant.Assistant.find_by_id", return_value=mock),
        patch("codemie.core.ability.Ability.can", return_value=True),
        patch("codemie.rest_api.routers.assistant.marketplace.is_approval_gate_armed", return_value=True),
        patch(
            "codemie.rest_api.routers.assistant.marketplace.config.MARKETPLACE_LLM_VALIDATION_ON_PUBLISH_ENABLED", False
        ),
        patch(
            "codemie.rest_api.routers.assistant.marketplace._publish_gate_findings",
            return_value=findings,
        ),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as ac:
            response = await ac.post(
                f"/v1/assistants/{assistant_id}/marketplace/publish/gate-check",
                json={
                    "name": "My Assistant",
                    "categories": ["engineering"],
                    "system_prompt": "short",
                    "description": "A description",
                },
                headers={"Authorization": "Bearer testtoken"},
            )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    error = response.json()["error"]
    assert "system_prompt_length" in error["message"]
    details = error["details"]
    assert details["assistant_id"] == assistant_id
    assert details["requires_confirmation"] is False
    assert "system_prompt_length" in details["failed_rules"]
    assert len(details["gate_findings"]) == 1
    assert details["gate_findings"][0]["rule"] == "system_prompt_length"


@pytest.mark.asyncio
async def test_gate_check_returns_422_when_categories_absent():
    """gate-check returns 422 when categories field is missing."""
    assistant_id = "gate-check-3"

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as ac:
        response = await ac.post(
            f"/v1/assistants/{assistant_id}/marketplace/publish/gate-check",
            json={"name": "My Assistant"},
            headers={"Authorization": "Bearer testtoken"},
        )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


@pytest.mark.asyncio
async def test_gate_check_returns_404_when_assistant_not_found():
    """gate-check returns 404 when assistant does not exist."""
    assistant_id = "nonexistent"

    with patch("codemie.rest_api.models.assistant.Assistant.find_by_id", return_value=None):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as ac:
            response = await ac.post(
                f"/v1/assistants/{assistant_id}/marketplace/publish/gate-check",
                json={"name": "My Assistant", "categories": ["engineering"]},
                headers={"Authorization": "Bearer testtoken"},
            )

    assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.asyncio
async def test_gate_check_returns_403_when_no_write_access():
    """gate-check returns 403 when user lacks write access."""
    assistant_id = "gate-check-4"
    mock = _gate_check_assistant_mock(assistant_id)

    with (
        patch("codemie.rest_api.models.assistant.Assistant.find_by_id", return_value=mock),
        patch("codemie.core.ability.Ability.can", return_value=False),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as ac:
            response = await ac.post(
                f"/v1/assistants/{assistant_id}/marketplace/publish/gate-check",
                json={"name": "My Assistant", "categories": ["engineering"]},
                headers={"Authorization": "Bearer testtoken"},
            )

    assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.asyncio
async def test_gate_check_overrides_assistant_fields_before_running_gates():
    """gate-check passes request body values to the gate engine, not persisted values."""
    assistant_id = "gate-check-5"
    mock = _gate_check_assistant_mock(assistant_id)
    mock.name = "Old Name"
    mock.system_prompt = "Old system prompt"

    captured_assistant = {}

    def capture_and_return(assistant):
        captured_assistant["name"] = assistant.name
        captured_assistant["system_prompt"] = assistant.system_prompt
        return []

    with (
        patch("codemie.rest_api.models.assistant.Assistant.find_by_id", return_value=mock),
        patch("codemie.core.ability.Ability.can", return_value=True),
        patch("codemie.rest_api.routers.assistant.marketplace.is_approval_gate_armed", return_value=True),
        patch(
            "codemie.rest_api.routers.assistant.marketplace.config.MARKETPLACE_LLM_VALIDATION_ON_PUBLISH_ENABLED", False
        ),
        patch(
            "codemie.rest_api.routers.assistant.marketplace._publish_gate_findings",
            side_effect=capture_and_return,
        ),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as ac:
            await ac.post(
                f"/v1/assistants/{assistant_id}/marketplace/publish/gate-check",
                json={
                    "name": "New Name",
                    "categories": ["engineering"],
                    "system_prompt": "New system prompt",
                },
                headers={"Authorization": "Bearer testtoken"},
            )

    assert captured_assistant["name"] == "New Name"
    assert captured_assistant["system_prompt"] == "New system prompt"
