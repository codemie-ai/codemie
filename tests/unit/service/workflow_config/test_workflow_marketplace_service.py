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

from codemie.core.exceptions import NotFoundException, ValidationException
from codemie.rest_api.models.assistant import InlineCredential
from codemie.rest_api.models.workflow_marketplace import PublishWorkflowToMarketplaceRequest
from codemie.service.workflow_config.workflow_marketplace_service import WorkflowMarketplaceService

_ENTITIES_PATCH = (
    "codemie.service.workflow_config.workflow_marketplace_service._entities_collector.collect_for_workflow"
)
_CREDENTIALS_PATCH = (
    "codemie.service.workflow_config.workflow_marketplace_service._credentials_collector.collect_for_workflow"
)
_CATEGORY_REPO_PATCH = "codemie.service.workflow_config.workflow_marketplace_service._category_repository.get_by_ids"


@pytest.fixture
def service() -> WorkflowMarketplaceService:
    return WorkflowMarketplaceService()


@pytest.fixture
def mock_workflow() -> MagicMock:
    wf = MagicMock()
    wf.id = "wf-1"
    return wf


@pytest.fixture
def mock_user() -> MagicMock:
    user = MagicMock()
    user.id = "u-1"
    return user


# ---------------------------------------------------------------------------
# validate — clean workflow
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_validate_clean_workflow_returns_no_confirmation(
    service: WorkflowMarketplaceService, mock_workflow: MagicMock, mock_user: MagicMock
) -> None:
    with (
        patch(_ENTITIES_PATCH, return_value=([], [])),
        patch(_CREDENTIALS_PATCH, return_value=[]),
    ):
        result = await service.validate(mock_workflow, mock_user)

    assert result.inline_credentials == []
    assert result.workflow_id == "wf-1"
    assert "ready to be published" in result.message


# ---------------------------------------------------------------------------
# validate — external entities block publication
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_validate_raises_when_external_assistants_found(
    service: WorkflowMarketplaceService, mock_workflow: MagicMock, mock_user: MagicMock
) -> None:
    external_assistant = MagicMock()

    with patch(_ENTITIES_PATCH, return_value=([external_assistant], [])):
        with pytest.raises(ValidationException):
            await service.validate(mock_workflow, mock_user)


@pytest.mark.asyncio
async def test_validate_raises_when_external_skills_found(
    service: WorkflowMarketplaceService, mock_workflow: MagicMock, mock_user: MagicMock
) -> None:
    external_skill = MagicMock()

    with patch(_ENTITIES_PATCH, return_value=([], [external_skill])):
        with pytest.raises(ValidationException):
            await service.validate(mock_workflow, mock_user)


@pytest.mark.asyncio
async def test_validate_raises_when_both_external_assistants_and_skills_found(
    service: WorkflowMarketplaceService, mock_workflow: MagicMock, mock_user: MagicMock
) -> None:
    with patch(_ENTITIES_PATCH, return_value=([MagicMock()], [MagicMock()])):
        with pytest.raises(ValidationException):
            await service.validate(mock_workflow, mock_user)


# ---------------------------------------------------------------------------
# validate — NotFoundException from collector is converted to ValidationException
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_validate_raises_validation_exception_when_collector_raises_not_found(
    service: WorkflowMarketplaceService, mock_workflow: MagicMock, mock_user: MagicMock
) -> None:
    with (
        patch(_ENTITIES_PATCH, side_effect=NotFoundException("entity not found")),
        pytest.raises(ValidationException, match="entity not found"),
    ):
        await service.validate(mock_workflow, mock_user)


# ---------------------------------------------------------------------------
# validate — inline credentials require confirmation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_validate_requires_confirmation_when_inline_mcp_credentials_present(
    service: WorkflowMarketplaceService, mock_workflow: MagicMock, mock_user: MagicMock
) -> None:
    cred = InlineCredential(mcp_server="my-mcp", credential_type="mcp_environment_vars")

    with (
        patch(_ENTITIES_PATCH, return_value=([], [])),
        patch(_CREDENTIALS_PATCH, return_value=[cred]),
    ):
        result = await service.validate(mock_workflow, mock_user)

    assert len(result.inline_credentials) == 1
    assert result.inline_credentials[0].mcp_server == "my-mcp"
    assert "inline credentials" in result.message


@pytest.mark.asyncio
async def test_validate_requires_confirmation_when_integration_alias_present(
    service: WorkflowMarketplaceService, mock_workflow: MagicMock, mock_user: MagicMock
) -> None:
    cred = InlineCredential(tool="my-tool", integration_alias="my-alias", credential_type="tool_integration_alias")

    with (
        patch(_ENTITIES_PATCH, return_value=([], [])),
        patch(_CREDENTIALS_PATCH, return_value=[cred]),
    ):
        result = await service.validate(mock_workflow, mock_user)

    assert result.inline_credentials[0].integration_alias == "my-alias"


@pytest.mark.asyncio
async def test_validate_returns_all_inline_credentials(
    service: WorkflowMarketplaceService, mock_workflow: MagicMock, mock_user: MagicMock
) -> None:
    creds = [
        InlineCredential(mcp_server="mcp-1", credential_type="mcp_environment_vars"),
        InlineCredential(tool="t", integration_alias="alias-1", credential_type="tool_integration_alias"),
    ]

    with (
        patch(_ENTITIES_PATCH, return_value=([], [])),
        patch(_CREDENTIALS_PATCH, return_value=creds),
    ):
        result = await service.validate(mock_workflow, mock_user)

    assert len(result.inline_credentials) == 2


# ---------------------------------------------------------------------------
# validate — response fields
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_validate_workflow_id_matches_workflow(
    service: WorkflowMarketplaceService, mock_workflow: MagicMock, mock_user: MagicMock
) -> None:
    mock_workflow.id = "wf-42"

    with (
        patch(_ENTITIES_PATCH, return_value=([], [])),
        patch(_CREDENTIALS_PATCH, return_value=[]),
    ):
        result = await service.validate(mock_workflow, mock_user)

    assert result.workflow_id == "wf-42"


# ---------------------------------------------------------------------------
# publish — helpers
# ---------------------------------------------------------------------------


def _make_category(cat_id: str) -> MagicMock:
    cat = MagicMock()
    cat.id = cat_id
    return cat


# ---------------------------------------------------------------------------
# publish — happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_publish_sets_is_global_and_categories(
    service: WorkflowMarketplaceService, mock_workflow: MagicMock, mock_user: MagicMock
) -> None:
    request = PublishWorkflowToMarketplaceRequest(categories=["cat-1", "cat-2"])

    with (
        patch(_ENTITIES_PATCH, return_value=([], [])),
        patch(_CREDENTIALS_PATCH, return_value=[]),
        patch(_CATEGORY_REPO_PATCH, return_value=[_make_category("cat-1"), _make_category("cat-2")]),
        patch(_CONFIG_REPO) as mock_config_repo,
    ):
        result = await service.publish(mock_workflow, request, mock_user)

    assert result is mock_workflow
    assert mock_workflow.is_global is True
    assert mock_workflow.categories == ["cat-1", "cat-2"]
    mock_config_repo.set_publish_state.assert_called_once_with(
        str(mock_workflow.id), is_global=True, categories=["cat-1", "cat-2"]
    )


# ---------------------------------------------------------------------------
# publish — invalid categories
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_publish_raises_when_category_ids_not_found(
    service: WorkflowMarketplaceService, mock_workflow: MagicMock, mock_user: MagicMock
) -> None:
    request = PublishWorkflowToMarketplaceRequest(categories=["cat-1", "missing"])

    with (
        patch(_ENTITIES_PATCH, return_value=([], [])),
        patch(_CREDENTIALS_PATCH, return_value=[]),
        patch(_CATEGORY_REPO_PATCH, return_value=[_make_category("cat-1")]),
        pytest.raises(ValidationException),
    ):
        await service.publish(mock_workflow, request, mock_user)


@pytest.mark.asyncio
async def test_publish_raises_when_all_category_ids_missing(
    service: WorkflowMarketplaceService, mock_workflow: MagicMock, mock_user: MagicMock
) -> None:
    request = PublishWorkflowToMarketplaceRequest(categories=["unknown"])

    with (
        patch(_ENTITIES_PATCH, return_value=([], [])),
        patch(_CREDENTIALS_PATCH, return_value=[]),
        patch(_CATEGORY_REPO_PATCH, return_value=[]),
        pytest.raises(ValidationException),
    ):
        await service.publish(mock_workflow, request, mock_user)


# ---------------------------------------------------------------------------
# publish — blocked by validation failures
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_publish_raises_when_external_assistants_present(
    service: WorkflowMarketplaceService, mock_workflow: MagicMock, mock_user: MagicMock
) -> None:
    request = PublishWorkflowToMarketplaceRequest(categories=["cat-1"])

    with (
        patch(_ENTITIES_PATCH, return_value=([MagicMock()], [])),
        patch(_CATEGORY_REPO_PATCH, return_value=[_make_category("cat-1")]),
        pytest.raises(ValidationException),
    ):
        await service.publish(mock_workflow, request, mock_user)


@pytest.mark.asyncio
async def test_publish_raises_when_external_skills_present(
    service: WorkflowMarketplaceService, mock_workflow: MagicMock, mock_user: MagicMock
) -> None:
    request = PublishWorkflowToMarketplaceRequest(categories=["cat-1"])

    with (
        patch(_ENTITIES_PATCH, return_value=([], [MagicMock()])),
        patch(_CATEGORY_REPO_PATCH, return_value=[_make_category("cat-1")]),
        pytest.raises(ValidationException),
    ):
        await service.publish(mock_workflow, request, mock_user)


@pytest.mark.asyncio
async def test_validate_confirmation_message_contains_credential_hint(
    service: WorkflowMarketplaceService, mock_workflow: MagicMock, mock_user: MagicMock
) -> None:
    cred = InlineCredential(mcp_server="s", credential_type="mcp_auth_token")

    with (
        patch(_ENTITIES_PATCH, return_value=([], [])),
        patch(_CREDENTIALS_PATCH, return_value=[cred]),
    ):
        result = await service.validate(mock_workflow, mock_user)

    assert "confirm" in result.message.lower() or "inline credentials" in result.message.lower()


# ---------------------------------------------------------------------------
# track_usage
# ---------------------------------------------------------------------------

_CONFIG_REPO = "codemie.service.workflow_config.workflow_marketplace_service._workflow_config_repository"

_WORKFLOW_ID = "wf-id-123"
_USER_ID = "user-id-456"


@patch(_CONFIG_REPO)
def test_track_usage_new_user_increments_counter(
    mock_config_repo: MagicMock,
    service: WorkflowMarketplaceService,
) -> None:
    """track_usage must recompute unique_users_count after each execution."""
    service.track_usage(_WORKFLOW_ID, _USER_ID)

    mock_config_repo.recompute_unique_users_count.assert_called_once_with(_WORKFLOW_ID)


@patch(_CONFIG_REPO)
def test_track_usage_existing_user_does_not_increment(
    mock_config_repo: MagicMock,
    service: WorkflowMarketplaceService,
) -> None:
    """Repeated calls must still recompute (idempotent atomic UPDATE)."""
    service.track_usage(_WORKFLOW_ID, _USER_ID)
    service.track_usage(_WORKFLOW_ID, _USER_ID)

    assert mock_config_repo.recompute_unique_users_count.call_count == 2


@patch(_CONFIG_REPO)
def test_track_usage_swallows_exception(
    mock_config_repo: MagicMock,
    service: WorkflowMarketplaceService,
) -> None:
    """Exception inside track_usage must not propagate to the caller (background task)."""
    mock_config_repo.recompute_unique_users_count.side_effect = RuntimeError("DB error")

    service.track_usage(_WORKFLOW_ID, _USER_ID)
