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

import pytest
from unittest.mock import patch, MagicMock
from codemie.rest_api.models.vendor import ImportAgentcoreRuntime
from codemie.core.exceptions import ExtendedHTTPException
from codemie.service.aws_bedrock.bedrock_agentcore_runtime_service import BedrockAgentCoreRuntimeService
from codemie.rest_api.models.settings import AWSCredentials, Settings
from codemie.rest_api.security.user import User


@pytest.fixture
def mock_user():
    user = MagicMock(spec=User)
    user.id = "user-id"
    user.username = "testuser"
    user.name = "Test User"
    user.project_names = ["proj1"]
    user.is_admin = False
    user.is_applications_admin = False
    return user


@pytest.fixture
def mock_setting():
    setting = MagicMock(spec=Settings)
    setting.id = "setting-1"
    setting.project_name = "proj1"
    setting.alias = "Test Setting"
    return setting


@pytest.fixture
def mock_aws_creds():
    return AWSCredentials(
        region="us-east-1",
        access_key_id="test-access-key",
        secret_access_key="test-secret-key",
    )


@pytest.fixture
def runtime_data():
    return [
        {
            "agentRuntimeId": "runtime-1",
            "agentRuntimeName": "Runtime 1",
            "status": "READY",
            "agentRuntimeVersion": "1",
            "description": "Test runtime 1",
            "lastUpdatedAt": "2024-01-01T00:00:00Z",
        },
        {
            "agentRuntimeId": "runtime-2",
            "agentRuntimeName": "Runtime 2",
            "status": "NOT_READY",
            "agentRuntimeVersion": "2",
            "description": "Test runtime 2",
            "lastUpdatedAt": "2024-01-02T00:00:00Z",
        },
    ]


@pytest.fixture
def endpoint_data():
    return [
        {
            "id": "endpoint-1",
            "name": "Endpoint 1",
            "status": "READY",
            "description": "Test endpoint 1",
            "liveVersion": "1",
            "targetVersion": "1",
            "createdAt": "2024-01-01T00:00:00Z",
            "lastUpdatedAt": "2024-01-01T00:00:00Z",
        },
        {
            "id": "endpoint-2",
            "name": "Endpoint 2",
            "status": "NOT_READY",
            "description": "Test endpoint 2",
            "liveVersion": "2",
            "targetVersion": "2",
            "createdAt": "2024-01-02T00:00:00Z",
            "lastUpdatedAt": "2024-01-02T00:00:00Z",
        },
    ]


@pytest.fixture
def endpoint_detail():
    return {
        "id": "endpoint-1",
        "name": "Endpoint 1",
        "status": "READY",
        "description": "Test endpoint description",
        "liveVersion": "1",
        "targetVersion": "1",
        "agentRuntimeEndpointArn": "arn:aws:bedrock:us-east-1:123456789012:runtime-endpoint/endpoint-1",
        "agentRuntimeArn": "arn:aws:bedrock:us-east-1:123456789012:runtime/runtime-1",
        "createdAt": "2024-01-01T00:00:00Z",
        "lastUpdatedAt": "2024-01-01T00:00:00Z",
    }


@pytest.fixture
def runtime_endpoint_import():
    return ImportAgentcoreRuntime(
        id="runtime-1",
        agentcoreRuntimeEndpointName="Endpoint 1",
        invocation_json='{"prompt": "__QUERY_PLACEHOLDER__"}',
        setting_id="setting-1",
    )


# --- Tests for get_all_settings_overview ---
@patch("codemie.service.aws_bedrock.bedrock_agentcore_runtime_service.get_all_settings_for_user")
@patch(
    "codemie.service.aws_bedrock.bedrock_agentcore_runtime_service.BedrockAgentCoreRuntimeService._fetch_main_entity_names_for_setting"
)
def test_get_all_settings_overview_success(
    mock_fetch_main_entity_names_for_setting,
    mock_get_all_settings_for_user,
    mock_user,
    runtime_data,
):
    """Test get_all_settings_overview returns correct overview for multiple settings."""
    # Mock settings
    setting1 = MagicMock()
    setting1.id = "setting-1"
    setting1.alias = "Setting 1"
    setting1.project_name = "project-1"

    setting2 = MagicMock()
    setting2.id = "setting-2"
    setting2.alias = "Setting 2"
    setting2.project_name = "project-2"

    mock_get_all_settings_for_user.return_value = [setting1, setting2]

    # Mock the fetch method to return runtime names for different settings
    mock_fetch_main_entity_names_for_setting.side_effect = [
        ["Runtime 1", "Runtime 2"],
        ["Runtime 3"],
    ]

    result = BedrockAgentCoreRuntimeService.get_all_settings_overview(mock_user, page=0, per_page=10)

    assert len(result["data"]) == 2
    assert result["data"][0]["setting_id"] == "setting-1"
    assert result["data"][0]["entities"] == ["Runtime 1", "Runtime 2"]
    assert result["data"][1]["setting_id"] == "setting-2"
    assert result["data"][1]["entities"] == ["Runtime 3"]


@patch("codemie.service.aws_bedrock.bedrock_agentcore_runtime_service.get_all_settings_for_user")
@patch(
    "codemie.service.aws_bedrock.bedrock_agentcore_runtime_service.BedrockAgentCoreRuntimeService._fetch_main_entity_names_for_setting"
)
def test_get_all_settings_overview_empty_settings(
    mock_fetch_main_entity_names_for_setting,
    mock_get_all_settings_for_user,
    mock_user,
):
    """Test get_all_settings_overview with no settings available."""
    mock_get_all_settings_for_user.return_value = []

    result = BedrockAgentCoreRuntimeService.get_all_settings_overview(mock_user, page=0, per_page=10)

    assert result["data"] == []
    assert result["pagination"]["total"] == 0
    assert result["pagination"]["pages"] == 0
    assert result["pagination"]["page"] == 0
    assert result["pagination"]["per_page"] == 10

    # Should not call the fetch method when there are no settings
    mock_fetch_main_entity_names_for_setting.assert_not_called()


@patch("codemie.service.aws_bedrock.bedrock_agentcore_runtime_service.get_all_settings_for_user")
@patch("codemie.service.aws_bedrock.bedrock_agentcore_runtime_service.get_setting_aws_credentials")
@patch(
    "codemie.service.aws_bedrock.bedrock_agentcore_runtime_service.BedrockAgentCoreRuntimeService._bedrock_list_agent_runtimes"
)
def test_get_all_settings_overview_limits_entity_count(
    mock_bedrock_list_agent_runtimes,
    mock_get_setting_aws_credentials,
    mock_get_all_settings_for_user,
    mock_user,
    mock_setting,
    mock_aws_creds,
):
    """Test get_all_settings_overview limits entity count to ALL_SETTINGS_OVERVIEW_ENTITY_COUNT."""
    mock_get_all_settings_for_user.return_value = [mock_setting]
    mock_get_setting_aws_credentials.return_value = mock_aws_creds

    # Create more than 4 runtimes
    many_runtimes = []
    for i in range(10):
        many_runtimes.append({"agentRuntimeName": f"Runtime {i}"})

    mock_bedrock_list_agent_runtimes.return_value = many_runtimes, None

    result = BedrockAgentCoreRuntimeService.get_all_settings_overview(mock_user, page=0, per_page=10)

    # Should only return 4 runtime names (ALL_SETTINGS_OVERVIEW_ENTITY_COUNT)
    assert len(result["data"]) == 1
    assert len(result["data"][0]["entities"]) == 4


# --- Tests for list_main_entities ---
@patch("codemie.service.aws_bedrock.bedrock_agentcore_runtime_service.get_setting_for_user")
@patch("codemie.service.aws_bedrock.bedrock_agentcore_runtime_service.get_setting_aws_credentials")
@patch(
    "codemie.service.aws_bedrock.bedrock_agentcore_runtime_service.BedrockAgentCoreRuntimeService._bedrock_list_agent_runtimes"
)
def test_list_main_entities_success(
    mock_bedrock_list_agent_runtimes,
    mock_get_setting_aws_credentials,
    mock_get_setting_for_user,
    mock_user,
    mock_setting,
    mock_aws_creds,
    runtime_data,
):
    """Test list_main_entities returns correct runtime data."""
    mock_get_setting_for_user.return_value = mock_setting
    mock_get_setting_aws_credentials.return_value = mock_aws_creds
    mock_bedrock_list_agent_runtimes.return_value = (runtime_data, None)

    result, next_token = BedrockAgentCoreRuntimeService.list_main_entities(mock_user, "setting-1", page=0, per_page=10)

    assert len(result) == 2
    assert result[0]["id"] == "runtime-1"
    assert result[0]["name"] == "Runtime 1"
    assert result[0]["status"] == "PREPARED"
    assert result[1]["id"] == "runtime-2"
    assert result[1]["name"] == "Runtime 2"
    assert result[1]["status"] == "NOT_PREPARED"
    assert next_token is None


@patch("codemie.service.aws_bedrock.bedrock_agentcore_runtime_service.get_setting_for_user")
@patch("codemie.service.aws_bedrock.bedrock_agentcore_runtime_service.get_setting_aws_credentials")
@patch(
    "codemie.service.aws_bedrock.bedrock_agentcore_runtime_service.BedrockAgentCoreRuntimeService._bedrock_list_agent_runtimes"
)
def test_list_main_entities_empty(
    mock_bedrock_list_agent_runtimes,
    mock_get_setting_aws_credentials,
    mock_get_setting_for_user,
    mock_user,
    mock_setting,
    mock_aws_creds,
):
    """Test list_main_entities with no runtimes."""
    mock_get_setting_for_user.return_value = mock_setting
    mock_get_setting_aws_credentials.return_value = mock_aws_creds
    mock_bedrock_list_agent_runtimes.return_value = ([], None)

    result, next_token = BedrockAgentCoreRuntimeService.list_main_entities(mock_user, "setting-1", page=0, per_page=10)

    assert result == []
    assert next_token is None


# --- Tests for get_main_entity_detail ---
@patch("codemie.service.aws_bedrock.bedrock_agentcore_runtime_service.get_setting_for_user")
@patch("codemie.service.aws_bedrock.bedrock_agentcore_runtime_service.get_setting_aws_credentials")
@patch(
    "codemie.service.aws_bedrock.bedrock_agentcore_runtime_service.BedrockAgentCoreRuntimeService._bedrock_get_agent_runtime"
)
def test_get_main_entity_detail_success(
    mock_bedrock_get_agent_runtime,
    mock_get_setting_aws_credentials,
    mock_get_setting_for_user,
    mock_user,
    mock_setting,
    mock_aws_creds,
):
    """Test get_main_entity_detail returns correct runtime information."""
    mock_get_setting_for_user.return_value = mock_setting
    mock_get_setting_aws_credentials.return_value = mock_aws_creds

    runtime_detail = {
        "agentRuntimeId": "runtime-1",
        "agentRuntimeName": "Runtime 1",
        "agentRuntimeVersion": "1",
        "description": "Test runtime description",
        "status": "READY",
        "lastUpdatedAt": "2024-01-01T00:00:00Z",
    }
    mock_bedrock_get_agent_runtime.return_value = runtime_detail

    result = BedrockAgentCoreRuntimeService.get_main_entity_detail(mock_user, "runtime-1", "setting-1")

    assert result["id"] == "runtime-1"
    assert result["name"] == "Runtime 1"
    assert result["description"] == "Test runtime description"
    assert result["status"] == "PREPARED"

    mock_get_setting_for_user.assert_called_once_with(mock_user, "setting-1")
    mock_get_setting_aws_credentials.assert_called_once_with(mock_setting.id)
    mock_bedrock_get_agent_runtime.assert_called_once_with(
        runtime_id="runtime-1",
        region=mock_aws_creds.region,
        access_key_id=mock_aws_creds.access_key_id,
        secret_access_key=mock_aws_creds.secret_access_key,
    )


@patch("codemie.service.aws_bedrock.bedrock_agentcore_runtime_service.get_setting_for_user")
@patch("codemie.service.aws_bedrock.bedrock_agentcore_runtime_service.get_setting_aws_credentials")
@patch(
    "codemie.service.aws_bedrock.bedrock_agentcore_runtime_service.BedrockAgentCoreRuntimeService._bedrock_get_agent_runtime"
)
def test_get_main_entity_detail_not_found(
    mock_bedrock_get_agent_runtime,
    mock_get_setting_aws_credentials,
    mock_get_setting_for_user,
    mock_user,
    mock_setting,
    mock_aws_creds,
):
    """Test get_main_entity_detail when runtime is not found."""
    mock_get_setting_for_user.return_value = mock_setting
    mock_get_setting_aws_credentials.return_value = mock_aws_creds
    mock_bedrock_get_agent_runtime.return_value = None

    with pytest.raises(ExtendedHTTPException):
        BedrockAgentCoreRuntimeService.get_main_entity_detail(mock_user, "runtime-1", "setting-1")


# --- Tests for list_importable_entities_for_main_entity ---
@patch("codemie.service.aws_bedrock.bedrock_agentcore_runtime_service.get_setting_for_user")
@patch("codemie.service.aws_bedrock.bedrock_agentcore_runtime_service.get_setting_aws_credentials")
@patch("codemie.service.aws_bedrock.bedrock_agentcore_runtime_service.Assistant.get_by_bedrock_runtime_aws_settings_id")
@patch(
    "codemie.service.aws_bedrock.bedrock_agentcore_runtime_service.BedrockAgentCoreRuntimeService._bedrock_list_runtime_endpoints"
)
def test_list_importable_entities_for_main_entity_success(
    mock_list_runtime_endpoints,
    mock_get_by_bedrock_runtime_aws_settings_id,
    mock_get_setting_aws_credentials,
    mock_get_setting_for_user,
    mock_user,
    mock_setting,
    mock_aws_creds,
    endpoint_data,
):
    """Test list_importable_entities_for_main_entity returns correct endpoints."""
    mock_get_setting_for_user.return_value = mock_setting
    mock_get_setting_aws_credentials.return_value = mock_aws_creds
    mock_get_by_bedrock_runtime_aws_settings_id.return_value = []
    mock_list_runtime_endpoints.return_value = (endpoint_data, None)

    result, next_token = BedrockAgentCoreRuntimeService.list_importable_entities_for_main_entity(
        mock_user, "runtime-1", "setting-1", page=0, per_page=10
    )

    assert len(result) == 2
    assert result[0]["id"] == "endpoint-1"
    assert result[0]["name"] == "Endpoint 1"
    assert result[0]["status"] == "PREPARED"
    assert result[1]["id"] == "endpoint-2"
    assert result[1]["status"] == "NOT_PREPARED"
    assert next_token is None


@patch("codemie.service.aws_bedrock.bedrock_agentcore_runtime_service.get_setting_for_user")
@patch("codemie.service.aws_bedrock.bedrock_agentcore_runtime_service.get_setting_aws_credentials")
@patch("codemie.service.aws_bedrock.bedrock_agentcore_runtime_service.Assistant.get_by_bedrock_runtime_aws_settings_id")
@patch(
    "codemie.service.aws_bedrock.bedrock_agentcore_runtime_service.BedrockAgentCoreRuntimeService._bedrock_list_runtime_endpoints"
)
def test_list_importable_entities_for_main_entity_with_existing_assistant(
    mock_list_runtime_endpoints,
    mock_get_by_bedrock_runtime_aws_settings_id,
    mock_get_setting_aws_credentials,
    mock_get_setting_for_user,
    mock_user,
    mock_setting,
    mock_aws_creds,
    endpoint_data,
):
    """Test list_importable_entities_for_main_entity includes aiRunId for existing assistants."""
    mock_get_setting_for_user.return_value = mock_setting
    mock_get_setting_aws_credentials.return_value = mock_aws_creds

    mock_assistant = MagicMock()
    mock_assistant.id = "assistant-1"
    mock_assistant.bedrock_agentcore_runtime = MagicMock()
    mock_assistant.bedrock_agentcore_runtime.runtime_endpoint_id = "endpoint-1"

    mock_assistant.bedrock_agentcore_runtime.runtime_endpoint_name = "Endpoint 1"

    mock_get_by_bedrock_runtime_aws_settings_id.return_value = [mock_assistant]
    mock_list_runtime_endpoints.return_value = (endpoint_data, None)

    result, next_token = BedrockAgentCoreRuntimeService.list_importable_entities_for_main_entity(
        mock_user, "runtime-1", "setting-1", page=0, per_page=10
    )

    # First endpoint should have aiRunId since it matches existing assistant by endpoint_id
    assert result[0]["id"] == "endpoint-1"
    assert "aiRunId" in result[0]
    assert result[0]["aiRunId"] == "assistant-1"

    # Second endpoint should not have aiRunId
    assert result[1]["id"] == "endpoint-2"
    assert "aiRunId" not in result[1]


@patch("codemie.service.aws_bedrock.bedrock_agentcore_runtime_service.get_setting_for_user")
@patch("codemie.service.aws_bedrock.bedrock_agentcore_runtime_service.get_setting_aws_credentials")
@patch("codemie.service.aws_bedrock.bedrock_agentcore_runtime_service.Assistant.get_by_bedrock_runtime_aws_settings_id")
@patch(
    "codemie.service.aws_bedrock.bedrock_agentcore_runtime_service.BedrockAgentCoreRuntimeService._bedrock_list_runtime_endpoints"
)
def test_list_importable_entities_for_main_entity_handles_exceptions(
    mock_list_runtime_endpoints,
    mock_get_by_bedrock_runtime_aws_settings_id,
    mock_get_setting_aws_credentials,
    mock_get_setting_for_user,
    mock_user,
    mock_setting,
    mock_aws_creds,
):
    """Test list_importable_entities_for_main_entity handles AWS exceptions."""
    mock_get_setting_for_user.return_value = mock_setting
    mock_get_setting_aws_credentials.return_value = mock_aws_creds
    mock_get_by_bedrock_runtime_aws_settings_id.return_value = []
    mock_list_runtime_endpoints.side_effect = Exception("AWS error")

    with pytest.raises(ExtendedHTTPException):
        BedrockAgentCoreRuntimeService.list_importable_entities_for_main_entity(
            mock_user, "runtime-1", "setting-1", page=0, per_page=10
        )


# --- Tests for get_importable_entity_detail ---
@patch("codemie.service.aws_bedrock.bedrock_agentcore_runtime_service.get_setting_for_user")
@patch("codemie.service.aws_bedrock.bedrock_agentcore_runtime_service.get_setting_aws_credentials")
@patch(
    "codemie.service.aws_bedrock.bedrock_agentcore_runtime_service.BedrockAgentCoreRuntimeService._bedrock_get_runtime_endpoint"
)
def test_get_importable_entity_detail_success(
    mock_bedrock_get_runtime_endpoint,
    mock_get_setting_aws_credentials,
    mock_get_setting_for_user,
    mock_user,
    mock_setting,
    mock_aws_creds,
    endpoint_detail,
):
    """Test get_importable_entity_detail returns correct endpoint information."""
    mock_get_setting_for_user.return_value = mock_setting
    mock_get_setting_aws_credentials.return_value = mock_aws_creds
    mock_bedrock_get_runtime_endpoint.return_value = endpoint_detail

    result = BedrockAgentCoreRuntimeService.get_importable_entity_detail(
        mock_user, "runtime-1", "Endpoint 1", "setting-1"
    )

    assert result["id"] == "endpoint-1"
    assert result["name"] == "Endpoint 1"
    assert result["status"] == "PREPARED"
    assert result["description"] == "Test endpoint description"


# --- Tests for import_entities ---
@patch("codemie.service.aws_bedrock.bedrock_agentcore_runtime_service.get_setting_for_user")
@patch("codemie.service.aws_bedrock.bedrock_agentcore_runtime_service.get_setting_aws_credentials")
@patch("codemie.service.aws_bedrock.bedrock_agentcore_runtime_service.Assistant.get_by_bedrock_runtime_aws_settings_id")
@patch(
    "codemie.service.aws_bedrock.bedrock_agentcore_runtime_service.BedrockAgentCoreRuntimeService._process_endpoint_import"
)
def test_import_entities_success(
    mock_process_endpoint_import,
    mock_get_by_bedrock_runtime_aws_settings_id,
    mock_get_setting_aws_credentials,
    mock_get_setting_for_user,
    mock_user,
):
    """Test import_entities calls _process_endpoint_import correctly."""
    # Setup mocks
    mock_setting = MagicMock()
    mock_setting.id = "setting-1"

    mock_get_setting_for_user.return_value = mock_setting
    mock_get_setting_aws_credentials.return_value = MagicMock(spec=AWSCredentials)
    mock_get_by_bedrock_runtime_aws_settings_id.return_value = []
    mock_process_endpoint_import.side_effect = [
        {"runtimeId": "runtime-1", "endpointName": "Endpoint 1", "aiRunId": "assistant-1"},
        {"runtimeId": "runtime-1", "endpointName": "Endpoint 2", "aiRunId": "assistant-2"},
    ]

    import_payload = {
        "setting-1": [
            ImportAgentcoreRuntime(
                id="runtime-1",
                agentcoreRuntimeEndpointName="Endpoint 1",
                invocation_json='{"prompt": "__QUERY_PLACEHOLDER__"}',
                setting_id="setting-1",
            ),
            ImportAgentcoreRuntime(
                id="runtime-1",
                agentcoreRuntimeEndpointName="Endpoint 2",
                invocation_json='{"text": "__QUERY_PLACEHOLDER__"}',
                setting_id="setting-1",
            ),
        ]
    }

    result = BedrockAgentCoreRuntimeService.import_entities(mock_user, import_payload)

    assert len(result) == 2
    assert result[0]["runtimeId"] == "runtime-1"
    assert result[1]["runtimeId"] == "runtime-1"
    assert mock_process_endpoint_import.call_count == 2


# --- Tests for delete_entities ---
@patch("codemie.service.aws_bedrock.bedrock_agentcore_runtime_service.Assistant.get_by_bedrock_runtime_aws_settings_id")
@patch("codemie.service.guardrail.guardrail_service.GuardrailService.remove_guardrail_assignments_for_entity")
def test_delete_entities_deletes_all_assistants(mock_remove_guardrails, mock_get_by_bedrock_runtime_aws_settings_id):
    """Test delete_entities deletes all assistants for a given setting_id."""
    from codemie.rest_api.models.assistant import AssistantType

    mock_remove_guardrails.return_value = None

    mock_assistant1 = MagicMock()
    mock_assistant1.type = AssistantType.BEDROCK_AGENTCORE_RUNTIME
    mock_assistant2 = MagicMock()
    mock_assistant2.type = AssistantType.BEDROCK_AGENTCORE_RUNTIME
    mock_get_by_bedrock_runtime_aws_settings_id.return_value = [mock_assistant1, mock_assistant2]

    BedrockAgentCoreRuntimeService.delete_entities("setting-1")

    mock_assistant1.delete.assert_called_once()
    mock_assistant2.delete.assert_called_once()
    mock_get_by_bedrock_runtime_aws_settings_id.assert_called_once_with("setting-1")


# --- Tests for validate_remote_entity_exists_and_cleanup ---
@patch("codemie.rest_api.models.settings.Settings.get_by_id")
@patch("codemie.service.aws_bedrock.bedrock_agentcore_runtime_service.get_setting_aws_credentials")
@patch(
    "codemie.service.aws_bedrock.bedrock_agentcore_runtime_service.BedrockAgentCoreRuntimeService._bedrock_get_runtime_endpoint"
)
def test_validate_remote_entity_exists_and_cleanup_entity_exists(
    mock_bedrock_get_runtime_endpoint,
    mock_get_setting_aws_credentials,
    mock_settings_get_by_id,
    mock_aws_creds,
):
    """Test validate_remote_entity_exists_and_cleanup when entity exists on remote."""
    from codemie.rest_api.models.assistant import AssistantType

    mock_settings_get_by_id.return_value = MagicMock(id="setting-1")
    mock_get_setting_aws_credentials.return_value = mock_aws_creds
    mock_bedrock_get_runtime_endpoint.return_value = {"id": "endpoint-1"}

    # Create mock assistant
    mock_assistant = MagicMock()
    mock_assistant.type = AssistantType.BEDROCK_AGENTCORE_RUNTIME
    mock_assistant.bedrock_agentcore_runtime = MagicMock()
    mock_assistant.bedrock_agentcore_runtime.runtime_id = "runtime-1"
    mock_assistant.bedrock_agentcore_runtime.runtime_endpoint_id = "endpoint-1"
    mock_assistant.bedrock_agentcore_runtime.runtime_endpoint_name = "Endpoint 1"
    mock_assistant.bedrock_agentcore_runtime.aws_settings_id = "setting-1"

    result = BedrockAgentCoreRuntimeService.validate_remote_entity_exists_and_cleanup(mock_assistant)

    assert result is None
    mock_assistant.delete.assert_not_called()


@patch("codemie.rest_api.models.settings.Settings.get_by_id")
@patch("codemie.service.aws_bedrock.bedrock_agentcore_runtime_service.get_setting_aws_credentials")
@patch(
    "codemie.service.aws_bedrock.bedrock_agentcore_runtime_service.BedrockAgentCoreRuntimeService._bedrock_get_runtime_endpoint"
)
@patch("codemie.service.guardrail.guardrail_service.GuardrailService.remove_guardrail_assignments_for_entity")
def test_validate_remote_entity_exists_and_cleanup_entity_not_found(
    mock_remove_guardrails,
    mock_bedrock_get_runtime_endpoint,
    mock_get_setting_aws_credentials,
    mock_settings_get_by_id,
    mock_aws_creds,
):
    """Test validate_remote_entity_exists_and_cleanup when entity is deleted on remote."""
    from botocore.exceptions import ClientError
    from codemie.rest_api.models.assistant import AssistantType

    mock_settings_get_by_id.return_value = MagicMock(id="setting-1")
    mock_get_setting_aws_credentials.return_value = mock_aws_creds

    # Mock ResourceNotFoundException
    error_response = {"Error": {"Code": "ResourceNotFoundException", "Message": "Endpoint not found"}}
    mock_bedrock_get_runtime_endpoint.side_effect = ClientError(error_response, "GetRuntimeEndpoint")

    # Create mock assistant
    mock_assistant = MagicMock()
    mock_assistant.type = AssistantType.BEDROCK_AGENTCORE_RUNTIME
    mock_assistant.bedrock_agentcore_runtime = MagicMock()
    mock_assistant.bedrock_agentcore_runtime.runtime_id = "runtime-1"
    mock_assistant.bedrock_agentcore_runtime.runtime_endpoint_id = "endpoint-1"
    mock_assistant.bedrock_agentcore_runtime.runtime_endpoint_name = "Endpoint 1"
    mock_assistant.bedrock_agentcore_runtime.aws_settings_id = "setting-1"
    mock_assistant.name = "Test Assistant"

    result = BedrockAgentCoreRuntimeService.validate_remote_entity_exists_and_cleanup(mock_assistant)

    assert result == "Test Assistant"
    mock_assistant.delete.assert_called_once()


@patch("codemie.rest_api.models.settings.Settings.get_by_id")
@patch("codemie.service.aws_bedrock.bedrock_agentcore_runtime_service.get_setting_aws_credentials")
@patch(
    "codemie.service.aws_bedrock.bedrock_agentcore_runtime_service.BedrockAgentCoreRuntimeService._bedrock_get_runtime_endpoint"
)
def test_validate_remote_entity_exists_and_cleanup_other_client_error(
    mock_bedrock_get_runtime_endpoint,
    mock_get_setting_aws_credentials,
    mock_settings_get_by_id,
    mock_aws_creds,
):
    """Test validate_remote_entity_exists_and_cleanup with other AWS client errors."""
    from botocore.exceptions import ClientError
    from codemie.rest_api.models.assistant import AssistantType

    mock_settings_get_by_id.return_value = MagicMock(id="setting-1")
    mock_get_setting_aws_credentials.return_value = mock_aws_creds

    # Mock other client error
    error_response = {"Error": {"Code": "AccessDeniedException", "Message": "Access denied"}}
    mock_bedrock_get_runtime_endpoint.side_effect = ClientError(error_response, "GetRuntimeEndpoint")

    # Create mock assistant
    mock_assistant = MagicMock()
    mock_assistant.type = AssistantType.BEDROCK_AGENTCORE_RUNTIME
    mock_assistant.bedrock_agentcore_runtime = MagicMock()
    mock_assistant.bedrock_agentcore_runtime.runtime_id = "runtime-1"
    mock_assistant.bedrock_agentcore_runtime.runtime_endpoint_id = "endpoint-1"
    mock_assistant.bedrock_agentcore_runtime.runtime_endpoint_name = "Endpoint 1"
    mock_assistant.bedrock_agentcore_runtime.aws_settings_id = "setting-1"
    mock_assistant.name = "Test Assistant"

    result = BedrockAgentCoreRuntimeService.validate_remote_entity_exists_and_cleanup(mock_assistant)

    assert result is None
    mock_assistant.delete.assert_not_called()


def test_validate_remote_entity_exists_and_cleanup_non_bedrock_assistant():
    """Test validate_remote_entity_exists_and_cleanup with non-Bedrock assistant."""
    # Create mock assistant without Bedrock configuration
    mock_assistant = MagicMock()
    mock_assistant.type = "codemie"
    mock_assistant.bedrock_agentcore_runtime = None

    result = BedrockAgentCoreRuntimeService.validate_remote_entity_exists_and_cleanup(mock_assistant)

    assert result is None
    mock_assistant.delete.assert_not_called()


def test_validate_remote_entity_exists_and_cleanup_missing_bedrock_fields():
    """Test validate_remote_entity_exists_and_cleanup with incomplete Bedrock configuration."""
    from codemie.rest_api.models.assistant import AssistantType

    # Create mock assistant with incomplete Bedrock configuration
    mock_assistant = MagicMock()
    mock_assistant.type = AssistantType.BEDROCK_AGENTCORE_RUNTIME
    mock_assistant.bedrock_agentcore_runtime = MagicMock()
    mock_assistant.bedrock_agentcore_runtime.runtime_id = None
    mock_assistant.bedrock_agentcore_runtime.runtime_endpoint_id = "endpoint-1"

    result = BedrockAgentCoreRuntimeService.validate_remote_entity_exists_and_cleanup(mock_assistant)

    assert result is None
    mock_assistant.delete.assert_not_called()


@patch("codemie.rest_api.models.settings.Settings.get_by_id")
def test_validate_remote_entity_exists_and_cleanup_setting_not_found(
    mock_settings_get_by_id,
):
    """Test validate_remote_entity_exists_and_cleanup when setting is not found."""
    from codemie.rest_api.models.assistant import AssistantType

    mock_settings_get_by_id.return_value = None

    mock_assistant = MagicMock()
    mock_assistant.type = AssistantType.BEDROCK_AGENTCORE_RUNTIME
    mock_assistant.bedrock_agentcore_runtime = MagicMock()
    mock_assistant.bedrock_agentcore_runtime.runtime_id = "runtime-1"
    mock_assistant.bedrock_agentcore_runtime.runtime_endpoint_id = "endpoint-1"
    mock_assistant.bedrock_agentcore_runtime.runtime_endpoint_name = "Endpoint 1"
    mock_assistant.bedrock_agentcore_runtime.aws_settings_id = "setting-1"

    BedrockAgentCoreRuntimeService.validate_remote_entity_exists_and_cleanup(mock_assistant)

    mock_assistant.delete.assert_not_called()


@patch("codemie.rest_api.models.settings.Settings.get_by_id")
def test_validate_remote_entity_exists_and_cleanup_unexpected_error(
    mock_settings_get_by_id,
):
    """Test validate_remote_entity_exists_and_cleanup with unexpected errors."""
    from codemie.rest_api.models.assistant import AssistantType

    mock_settings_get_by_id.side_effect = Exception("Unexpected error")

    mock_assistant = MagicMock()
    mock_assistant.type = AssistantType.BEDROCK_AGENTCORE_RUNTIME
    mock_assistant.bedrock_agentcore_runtime = MagicMock()
    mock_assistant.bedrock_agentcore_runtime.runtime_id = "runtime-1"
    mock_assistant.bedrock_agentcore_runtime.runtime_endpoint_id = "endpoint-1"
    mock_assistant.bedrock_agentcore_runtime.runtime_endpoint_name = "Endpoint 1"
    mock_assistant.bedrock_agentcore_runtime.aws_settings_id = "setting-1"
    mock_assistant.name = "Test Assistant"

    BedrockAgentCoreRuntimeService.validate_remote_entity_exists_and_cleanup(mock_assistant)

    mock_assistant.delete.assert_not_called()


# --- Tests for invoke_agentcore_runtime ---
@patch("codemie.service.aws_bedrock.bedrock_agentcore_runtime_service.get_setting_aws_credentials")
@patch(
    "codemie.service.aws_bedrock.bedrock_agentcore_runtime_service.BedrockAgentCoreRuntimeService._prepare_invocation_payload"
)
@patch(
    "codemie.service.aws_bedrock.bedrock_agentcore_runtime_service.BedrockAgentCoreRuntimeService._bedrock_invoke_runtime"
)
def test_invoke_agentcore_runtime_success(
    mock_bedrock_invoke_runtime,
    mock_prepare_invocation_payload,
    mock_get_setting_aws_credentials,
    mock_aws_creds,
):
    """Test invoke_agentcore_runtime successfully invokes a runtime."""
    from codemie.rest_api.models.assistant import AssistantType

    mock_get_setting_aws_credentials.return_value = mock_aws_creds
    mock_prepare_invocation_payload.return_value = b'{"prompt": "test query"}'
    mock_bedrock_invoke_runtime.return_value = "Test response from runtime"

    # Create mock assistant
    mock_assistant = MagicMock()
    mock_assistant.type = AssistantType.BEDROCK_AGENTCORE_RUNTIME
    mock_assistant.bedrock_agentcore_runtime = MagicMock()
    mock_assistant.bedrock_agentcore_runtime.runtime_arn = "arn:aws:bedrock:us-east-1:123456789012:runtime/runtime-1"
    mock_assistant.bedrock_agentcore_runtime.runtime_endpoint_name = "Endpoint 1"
    mock_assistant.bedrock_agentcore_runtime.aws_settings_id = "setting-1"
    mock_assistant.bedrock_agentcore_runtime.invocation_json = '{"prompt": "__QUERY_PLACEHOLDER__"}'

    response = BedrockAgentCoreRuntimeService.invoke_agentcore_runtime(
        assistant=mock_assistant,
        input_text="test query",
        conversation_id="test-conversation-id",
    )

    assert response["output"] == "Test response from runtime"
    assert "time_elapsed" in response
    mock_bedrock_invoke_runtime.assert_called_once()


@patch("codemie.service.aws_bedrock.bedrock_agentcore_runtime_service.get_setting_aws_credentials")
@patch(
    "codemie.service.aws_bedrock.bedrock_agentcore_runtime_service.BedrockAgentCoreRuntimeService._prepare_invocation_payload"
)
@patch(
    "codemie.service.aws_bedrock.bedrock_agentcore_runtime_service.BedrockAgentCoreRuntimeService._bedrock_invoke_runtime"
)
def test_invoke_agentcore_runtime_client_error(
    mock_bedrock_invoke_runtime,
    mock_prepare_invocation_payload,
    mock_get_setting_aws_credentials,
    mock_aws_creds,
):
    """Test invoke_agentcore_runtime handles client errors gracefully."""
    from botocore.exceptions import ClientError
    from codemie.rest_api.models.assistant import AssistantType

    mock_get_setting_aws_credentials.return_value = mock_aws_creds
    mock_prepare_invocation_payload.return_value = b'{"prompt": "test query"}'
    mock_bedrock_invoke_runtime.side_effect = ClientError(
        {"Error": {"Code": "400", "Message": "Bad Request"}}, "invoke_runtime"
    )

    # Create mock assistant
    mock_assistant = MagicMock()
    mock_assistant.type = AssistantType.BEDROCK_AGENTCORE_RUNTIME
    mock_assistant.bedrock_agentcore_runtime = MagicMock()
    mock_assistant.bedrock_agentcore_runtime.runtime_arn = "arn:aws:bedrock:us-east-1:123456789012:runtime/runtime-1"
    mock_assistant.bedrock_agentcore_runtime.runtime_endpoint_name = "Endpoint 1"
    mock_assistant.bedrock_agentcore_runtime.aws_settings_id = "setting-1"
    mock_assistant.bedrock_agentcore_runtime.invocation_json = '{"prompt": "__QUERY_PLACEHOLDER__"}'

    response = BedrockAgentCoreRuntimeService.invoke_agentcore_runtime(
        assistant=mock_assistant,
        input_text="test query",
        conversation_id="test-conversation-id",
    )

    assert "error" in response["output"].lower() or "clienterror" in response["output"].lower()
    assert "time_elapsed" in response


# --- Tests for _validate_invocation_json ---
def test_validate_invocation_json_valid():
    """Test _validate_invocation_json with valid JSON."""
    valid_json = '{"prompt": "__QUERY_PLACEHOLDER__"}'
    result = BedrockAgentCoreRuntimeService._validate_invocation_json(valid_json)
    assert result is None


def test_validate_invocation_json_missing_placeholder():
    """Test _validate_invocation_json when placeholder is missing."""
    invalid_json = '{"prompt": "some text"}'
    result = BedrockAgentCoreRuntimeService._validate_invocation_json(invalid_json)
    assert result is not None
    assert "__QUERY_PLACEHOLDER__" in result


def test_validate_invocation_json_invalid_json():
    """Test _validate_invocation_json with invalid JSON."""
    invalid_json = '{"prompt": "__QUERY_PLACEHOLDER__"'  # Missing closing brace
    result = BedrockAgentCoreRuntimeService._validate_invocation_json(invalid_json)
    assert result is not None
    assert "Invalid JSON" in result


def test_validate_invocation_json_none():
    """Test _validate_invocation_json with None input."""
    result = BedrockAgentCoreRuntimeService._validate_invocation_json(None)
    assert result is None


def test_validate_invocation_json_nested_structure():
    """Test _validate_invocation_json with nested structure."""
    valid_nested = '{"input": {"text": "__QUERY_PLACEHOLDER__", "metadata": {}}}'
    result = BedrockAgentCoreRuntimeService._validate_invocation_json(valid_nested)
    assert result is None


# --- Tests for _contains_placeholder ---
def test_contains_placeholder_string():
    """Test _contains_placeholder with string value."""
    assert BedrockAgentCoreRuntimeService._contains_placeholder("__QUERY_PLACEHOLDER__") is True
    assert BedrockAgentCoreRuntimeService._contains_placeholder("other text") is False


def test_contains_placeholder_dict():
    """Test _contains_placeholder with dictionary."""
    assert BedrockAgentCoreRuntimeService._contains_placeholder({"key": "__QUERY_PLACEHOLDER__"}) is True
    assert BedrockAgentCoreRuntimeService._contains_placeholder({"key": "value"}) is False


def test_contains_placeholder_list():
    """Test _contains_placeholder with list."""
    assert BedrockAgentCoreRuntimeService._contains_placeholder(["__QUERY_PLACEHOLDER__"]) is True
    assert BedrockAgentCoreRuntimeService._contains_placeholder(["value"]) is False


def test_contains_placeholder_nested():
    """Test _contains_placeholder with nested structure."""
    nested = {"outer": {"inner": ["__QUERY_PLACEHOLDER__"]}}
    assert BedrockAgentCoreRuntimeService._contains_placeholder(nested) is True


# --- Tests for _replace_placeholder_in_structure ---
def test_replace_placeholder_string():
    """Test _replace_placeholder_in_structure with string."""
    result = BedrockAgentCoreRuntimeService._replace_placeholder_in_structure("__QUERY_PLACEHOLDER__", "test query")
    assert result == "test query"


def test_replace_placeholder_dict():
    """Test _replace_placeholder_in_structure with dictionary."""
    input_dict = {"prompt": "__QUERY_PLACEHOLDER__", "other": "value"}
    result = BedrockAgentCoreRuntimeService._replace_placeholder_in_structure(input_dict, "test query")
    assert result["prompt"] == "test query"
    assert result["other"] == "value"


def test_replace_placeholder_list():
    """Test _replace_placeholder_in_structure with list."""
    input_list = ["__QUERY_PLACEHOLDER__", "other"]
    result = BedrockAgentCoreRuntimeService._replace_placeholder_in_structure(input_list, "test query")
    assert result[0] == "test query"
    assert result[1] == "other"


def test_replace_placeholder_nested():
    """Test _replace_placeholder_in_structure with nested structure."""
    nested = {"outer": {"inner": ["__QUERY_PLACEHOLDER__"]}}
    result = BedrockAgentCoreRuntimeService._replace_placeholder_in_structure(nested, "test query")
    assert result["outer"]["inner"][0] == "test query"


# --- Tests for _prepare_invocation_payload ---
def test_prepare_invocation_payload_with_template():
    """Test _prepare_invocation_payload with valid template."""
    template = '{"prompt": "__QUERY_PLACEHOLDER__"}'
    result = BedrockAgentCoreRuntimeService._prepare_invocation_payload(template, "test query", "conv-123")

    import json

    payload = json.loads(result.decode("utf-8"))
    assert payload["prompt"] == "test query"


def test_prepare_invocation_payload_fallback():
    """Test _prepare_invocation_payload falls back to default when template is invalid."""
    invalid_template = '{"prompt": "__QUERY_PLACEHOLDER__"'  # Invalid JSON
    result = BedrockAgentCoreRuntimeService._prepare_invocation_payload(invalid_template, "test query", "conv-123")

    import json

    payload = json.loads(result.decode("utf-8"))
    assert payload["message"] == "test query"
    assert payload["sessionId"] == "conv-123"


def test_prepare_invocation_payload_no_template():
    """Test _prepare_invocation_payload with no template."""
    result = BedrockAgentCoreRuntimeService._prepare_invocation_payload(None, "test query", "conv-123")

    import json

    payload = json.loads(result.decode("utf-8"))
    assert payload["message"] == "test query"
    assert payload["sessionId"] == "conv-123"


# --- Tests for _process_endpoint_import ---
@patch(
    "codemie.service.aws_bedrock.bedrock_agentcore_runtime_service.BedrockAgentCoreRuntimeService._validate_invocation_json"
)
@patch(
    "codemie.service.aws_bedrock.bedrock_agentcore_runtime_service.BedrockAgentCoreRuntimeService._bedrock_get_runtime_endpoint"
)
@patch(
    "codemie.service.aws_bedrock.bedrock_agentcore_runtime_service.BedrockAgentCoreRuntimeService._create_assistant_data"
)
@patch(
    "codemie.service.aws_bedrock.bedrock_agentcore_runtime_service.BedrockAgentCoreRuntimeService._create_or_update_entity"
)
def test_process_endpoint_import_success(
    mock_create_or_update_entity,
    mock_create_assistant_data,
    mock_bedrock_get_runtime_endpoint,
    mock_validate_invocation_json,
    mock_user,
    mock_setting,
    mock_aws_creds,
):
    """Test _process_endpoint_import successfully imports an endpoint."""
    mock_validate_invocation_json.return_value = None
    mock_bedrock_get_runtime_endpoint.return_value = {
        "id": "endpoint-1",
        "agentRuntimeEndpointArn": "arn:aws:bedrock:us-east-1:123456789012:runtime-endpoint/endpoint-1",
        "status": "READY",
        "name": "Endpoint 1",
    }
    mock_create_assistant_data.return_value = {"name": "Test Assistant"}
    mock_create_or_update_entity.return_value = "assistant-1"

    result = BedrockAgentCoreRuntimeService._process_endpoint_import(
        user=mock_user,
        setting=mock_setting,
        aws_creds=mock_aws_creds,
        existing_entities_map={},
        input_runtime_id="runtime-1",
        input_endpoint_name="Endpoint 1",
        invocation_json='{"prompt": "__QUERY_PLACEHOLDER__"}',
    )

    assert result["runtimeId"] == "runtime-1"
    assert result["endpointName"] == "Endpoint 1"
    assert result["aiRunId"] == "assistant-1"
    assert "error" not in result


@patch(
    "codemie.service.aws_bedrock.bedrock_agentcore_runtime_service.BedrockAgentCoreRuntimeService._validate_invocation_json"
)
def test_process_endpoint_import_invalid_json(
    mock_validate_invocation_json,
    mock_user,
    mock_setting,
    mock_aws_creds,
):
    """Test _process_endpoint_import with invalid invocation JSON."""
    mock_validate_invocation_json.return_value = "Invalid JSON template"

    result = BedrockAgentCoreRuntimeService._process_endpoint_import(
        user=mock_user,
        setting=mock_setting,
        aws_creds=mock_aws_creds,
        existing_entities_map={},
        input_runtime_id="runtime-1",
        input_endpoint_name="Endpoint 1",
        invocation_json='{"prompt": "no placeholder"}',
    )

    assert "error" in result
    assert result["error"]["statusCode"] == "400"


@patch(
    "codemie.service.aws_bedrock.bedrock_agentcore_runtime_service.BedrockAgentCoreRuntimeService._validate_invocation_json"
)
@patch(
    "codemie.service.aws_bedrock.bedrock_agentcore_runtime_service.BedrockAgentCoreRuntimeService._bedrock_get_runtime_endpoint"
)
def test_process_endpoint_import_endpoint_not_ready(
    mock_bedrock_get_runtime_endpoint,
    mock_validate_invocation_json,
    mock_user,
    mock_setting,
    mock_aws_creds,
):
    """Test _process_endpoint_import when endpoint is not in READY status."""
    mock_validate_invocation_json.return_value = None
    mock_bedrock_get_runtime_endpoint.return_value = {
        "id": "endpoint-1",
        "agentRuntimeEndpointArn": "arn:aws:bedrock:us-east-1:123456789012:runtime-endpoint/endpoint-1",
        "status": "NOT_READY",
        "name": "Endpoint 1",
    }

    result = BedrockAgentCoreRuntimeService._process_endpoint_import(
        user=mock_user,
        setting=mock_setting,
        aws_creds=mock_aws_creds,
        existing_entities_map={},
        input_runtime_id="runtime-1",
        input_endpoint_name="Endpoint 1",
        invocation_json='{"prompt": "__QUERY_PLACEHOLDER__"}',
    )

    assert "error" in result
    assert result["error"]["statusCode"] == "409"
    assert "not in READY status" in result["error"]["message"]


# --- Tests for _create_assistant_data ---
def test_create_assistant_data(mock_user, mock_setting):
    """Test _create_assistant_data creates correct assistant data structure."""
    endpoint_info = {
        "id": "endpoint-1",
        "name": "Endpoint 1",
        "description": "Test endpoint",
        "agentRuntimeArn": "arn:aws:bedrock:us-east-1:123456789012:runtime/runtime-1",
        "agentRuntimeEndpointArn": "arn:aws:bedrock:us-east-1:123456789012:runtime-endpoint/endpoint-1",
        "liveVersion": "1",
    }

    result = BedrockAgentCoreRuntimeService._create_assistant_data(
        user=mock_user,
        setting=mock_setting,
        input_runtime_id="runtime-1",
        endpoint_info=endpoint_info,
        invocation_json='{"prompt": "__QUERY_PLACEHOLDER__"}',
    )

    assert result["name"] == "runtime-1:Endpoint 1"
    assert result["description"] == "Test endpoint"
    assert "bedrock_agentcore_runtime" in result
    assert result["bedrock_agentcore_runtime"]["runtime_id"] == "runtime-1"
    assert result["bedrock_agentcore_runtime"]["runtime_endpoint_name"] == "Endpoint 1"
    assert result["bedrock_agentcore_runtime"]["invocation_json"] == '{"prompt": "__QUERY_PLACEHOLDER__"}'
    assert result["type"] == "bedrock_agentcore_runtime"


# --- Tests for _create_or_update_entity ---
@patch("codemie.service.aws_bedrock.bedrock_agentcore_runtime_service.ensure_application_exists")
@patch("codemie.service.aws_bedrock.bedrock_agentcore_runtime_service.Assistant")
def test_create_or_update_entity_creates_new(
    mock_assistant_class,
    mock_ensure_application_exists,
):
    """Test _create_or_update_entity creates a new assistant."""
    mock_assistant_instance = MagicMock()
    mock_assistant_instance.id = "assistant-1"
    mock_assistant_class.return_value = mock_assistant_instance

    assistant_data = {
        "name": "Test Assistant",
        "project": "test-project",
    }

    result = BedrockAgentCoreRuntimeService._create_or_update_entity(
        endpoint_id="endpoint-1",
        assistant_data=assistant_data,
        existing_entities_map={},
        runtime_id="runtime-1",
    )

    assert result == "assistant-1"
    mock_ensure_application_exists.assert_called_once_with("test-project")
    mock_assistant_instance.save.assert_called_once()


def test_create_or_update_entity_updates_existing():
    """Test _create_or_update_entity updates an existing assistant."""
    mock_assistant = MagicMock()
    mock_assistant.id = "assistant-1"

    existing_entities_map = {"endpoint-1": mock_assistant}

    assistant_data = {
        "name": "Updated Assistant",
        "project": "test-project",
    }

    result = BedrockAgentCoreRuntimeService._create_or_update_entity(
        endpoint_id="endpoint-1",
        assistant_data=assistant_data,
        existing_entities_map=existing_entities_map,
        runtime_id="runtime-1",
    )

    assert result == "assistant-1"
    assert mock_assistant.name == "Updated Assistant"
    mock_assistant.save.assert_called_once()
