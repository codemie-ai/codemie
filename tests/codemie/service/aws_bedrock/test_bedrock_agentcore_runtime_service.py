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

import json
import pytest
from unittest.mock import patch, MagicMock
from codemie.rest_api.models.assistant import BedrockAgentcoreRuntimeData
from codemie.rest_api.models.guardrail import GuardrailEntity
from codemie.rest_api.models.vendor import ImportAgentcoreRuntime
from codemie.core.exceptions import ExtendedHTTPException
from codemie.service.aws_bedrock.exceptions import EntityNotFound, EntityAccessDenied
from codemie.service.aws_bedrock.bedrock_agentcore_runtime_service import BedrockAgentCoreRuntimeService
from codemie.service.aws_bedrock.agentcore.bedrock_agentcore_endpoint_service import BedrockAgentCoreEndpointService
from codemie.rest_api.models.settings import AWSCredentials, Settings
from codemie.rest_api.security.user import User

_ENDPOINT_MOD = "codemie.service.aws_bedrock.agentcore.bedrock_agentcore_endpoint_service"
_RUNTIME_MOD = "codemie.service.aws_bedrock.bedrock_agentcore_runtime_service"


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
        configuration_json='{"prompt": "__QUERY_PLACEHOLDER__"}',
        setting_id="setting-1",
    )


# --- Tests for get_all_settings_overview ---
@patch(f"{_RUNTIME_MOD}.get_all_settings_for_user")
@patch(f"{_RUNTIME_MOD}.BedrockAgentCoreRuntimeService._fetch_main_entity_names_for_setting")
def test_get_all_settings_overview_success(
    mock_fetch_main_entity_names_for_setting,
    mock_get_all_settings_for_user,
    mock_user,
    runtime_data,
):
    """Test get_all_settings_overview returns correct overview for multiple settings."""
    setting1 = MagicMock()
    setting1.id = "setting-1"
    setting1.alias = "Setting 1"
    setting1.project_name = "project-1"

    setting2 = MagicMock()
    setting2.id = "setting-2"
    setting2.alias = "Setting 2"
    setting2.project_name = "project-2"

    mock_get_all_settings_for_user.return_value = [setting1, setting2]
    mock_fetch_main_entity_names_for_setting.side_effect = [
        (["Runtime 1", "Runtime 2"], False),
        (["Runtime 3"], False),
    ]

    result = BedrockAgentCoreRuntimeService.get_all_settings_overview(mock_user, page=0, per_page=10)

    assert len(result["data"]) == 2
    assert result["data"][0]["setting_id"] == "setting-1"
    assert result["data"][0]["entities"] == ["Runtime 1", "Runtime 2"]
    assert result["data"][1]["setting_id"] == "setting-2"
    assert result["data"][1]["entities"] == ["Runtime 3"]


@patch(f"{_RUNTIME_MOD}.get_all_settings_for_user")
@patch(f"{_RUNTIME_MOD}.BedrockAgentCoreRuntimeService._fetch_main_entity_names_for_setting")
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

    mock_fetch_main_entity_names_for_setting.assert_not_called()


@patch(f"{_RUNTIME_MOD}.BedrockAgentCoreRuntimeService._get_deleted_runtime_entities")
@patch(f"{_RUNTIME_MOD}.get_all_settings_for_user")
@patch(f"{_RUNTIME_MOD}.get_setting_aws_credentials")
@patch(f"{_RUNTIME_MOD}.BedrockAgentCoreRuntimeService._bedrock_list_agent_runtimes")
def test_get_all_settings_overview_limits_entity_count(
    mock_bedrock_list_agent_runtimes,
    mock_get_setting_aws_credentials,
    mock_get_all_settings_for_user,
    mock_get_deleted_runtime_entities,
    mock_user,
    mock_setting,
    mock_aws_creds,
):
    """Test get_all_settings_overview limits entity count to ALL_SETTINGS_OVERVIEW_ENTITY_COUNT."""
    mock_get_all_settings_for_user.return_value = [mock_setting]
    mock_get_setting_aws_credentials.return_value = mock_aws_creds
    mock_get_deleted_runtime_entities.return_value = []

    many_runtimes = [{"agentRuntimeName": f"Runtime {i}"} for i in range(10)]
    mock_bedrock_list_agent_runtimes.return_value = many_runtimes, None

    result = BedrockAgentCoreRuntimeService.get_all_settings_overview(mock_user, page=0, per_page=10)

    assert len(result["data"]) == 1
    assert len(result["data"][0]["entities"]) == 4


# --- Tests for list_main_entities ---
@patch(f"{_RUNTIME_MOD}.BedrockAgentCoreRuntimeService._get_deleted_runtime_entities")
@patch(f"{_RUNTIME_MOD}.get_setting_for_user")
@patch(f"{_RUNTIME_MOD}.get_setting_aws_credentials")
@patch(f"{_RUNTIME_MOD}.BedrockAgentCoreRuntimeService._bedrock_list_agent_runtimes")
def test_list_main_entities_success(
    mock_bedrock_list_agent_runtimes,
    mock_get_setting_aws_credentials,
    mock_get_setting_for_user,
    mock_get_deleted_runtime_entities,
    mock_user,
    mock_setting,
    mock_aws_creds,
    runtime_data,
):
    """Test list_main_entities returns correct runtime data."""
    mock_get_setting_for_user.return_value = mock_setting
    mock_get_setting_aws_credentials.return_value = mock_aws_creds
    mock_bedrock_list_agent_runtimes.return_value = (runtime_data, None)
    mock_get_deleted_runtime_entities.return_value = []

    result, next_token = BedrockAgentCoreRuntimeService.list_main_entities(mock_user, "setting-1", page=0, per_page=10)

    assert len(result) == 2
    assert result[0].id == "runtime-1"
    assert result[0].name == "Runtime 1"
    assert result[0].status == "PREPARED"
    assert result[1].id == "runtime-2"
    assert result[1].name == "Runtime 2"
    assert result[1].status == "NOT_PREPARED"
    assert next_token is None


@patch(f"{_RUNTIME_MOD}.BedrockAgentCoreRuntimeService._get_deleted_runtime_entities")
@patch(f"{_RUNTIME_MOD}.get_setting_for_user")
@patch(f"{_RUNTIME_MOD}.get_setting_aws_credentials")
@patch(f"{_RUNTIME_MOD}.BedrockAgentCoreRuntimeService._bedrock_list_agent_runtimes")
def test_list_main_entities_empty(
    mock_bedrock_list_agent_runtimes,
    mock_get_setting_aws_credentials,
    mock_get_setting_for_user,
    mock_get_deleted_runtime_entities,
    mock_user,
    mock_setting,
    mock_aws_creds,
):
    """Test list_main_entities with no runtimes."""
    mock_get_setting_for_user.return_value = mock_setting
    mock_get_setting_aws_credentials.return_value = mock_aws_creds
    mock_bedrock_list_agent_runtimes.return_value = ([], None)
    mock_get_deleted_runtime_entities.return_value = []

    result, next_token = BedrockAgentCoreRuntimeService.list_main_entities(mock_user, "setting-1", page=0, per_page=10)

    assert result == []
    assert next_token is None


# --- Tests for get_main_entity_detail ---
@patch(f"{_RUNTIME_MOD}.get_setting_for_user")
@patch(f"{_RUNTIME_MOD}.get_setting_aws_credentials")
@patch(f"{_RUNTIME_MOD}.BedrockAgentCoreRuntimeService._bedrock_get_agent_runtime")
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

    assert result.id == "runtime-1"
    assert result.name == "Runtime 1"
    assert result.description == "Test runtime description"
    assert result.status == "PREPARED"

    mock_get_setting_for_user.assert_called_once_with(mock_user, "setting-1")
    mock_get_setting_aws_credentials.assert_called_once_with(mock_setting.id)
    mock_bedrock_get_agent_runtime.assert_called_once_with(
        runtime_id="runtime-1",
        region=mock_aws_creds.region,
        access_key_id=mock_aws_creds.access_key_id,
        secret_access_key=mock_aws_creds.secret_access_key,
        session_token=mock_aws_creds.session_token,
    )


@patch(f"{_RUNTIME_MOD}.Assistant.get_by_bedrock_runtime_aws_settings_id")
@patch(f"{_RUNTIME_MOD}.get_setting_for_user")
@patch(f"{_RUNTIME_MOD}.get_setting_aws_credentials")
@patch(f"{_RUNTIME_MOD}.BedrockAgentCoreRuntimeService._bedrock_get_agent_runtime")
def test_get_main_entity_detail_not_found(
    mock_bedrock_get_agent_runtime,
    mock_get_setting_aws_credentials,
    mock_get_setting_for_user,
    mock_get_by_runtime,
    mock_user,
    mock_setting,
    mock_aws_creds,
):
    """Test get_main_entity_detail re-raises when runtime is deleted on AWS and no imported assistants."""
    from botocore.exceptions import ClientError

    mock_get_setting_for_user.return_value = mock_setting
    mock_get_setting_aws_credentials.return_value = mock_aws_creds
    error_response = {"Error": {"Code": "ResourceNotFoundException", "Message": "Runtime not found"}}
    mock_bedrock_get_agent_runtime.side_effect = ClientError(error_response, "GetAgentRuntime")
    mock_get_by_runtime.return_value = []

    with pytest.raises(ExtendedHTTPException):
        BedrockAgentCoreRuntimeService.get_main_entity_detail(mock_user, "runtime-1", "setting-1")


# --- Tests for list_importable_entities_for_main_entity ---
@patch(f"{_ENDPOINT_MOD}.get_setting_for_user")
@patch(f"{_ENDPOINT_MOD}.get_setting_aws_credentials")
@patch(f"{_ENDPOINT_MOD}.Assistant.get_by_bedrock_runtime_aws_settings_id")
@patch(f"{_ENDPOINT_MOD}.BedrockAgentCoreEndpointService._bedrock_list_runtime_endpoints")
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


@patch(f"{_ENDPOINT_MOD}.get_setting_for_user")
@patch(f"{_ENDPOINT_MOD}.get_setting_aws_credentials")
@patch(f"{_ENDPOINT_MOD}.Assistant.get_by_bedrock_runtime_aws_settings_id")
@patch(f"{_ENDPOINT_MOD}.BedrockAgentCoreEndpointService._bedrock_list_runtime_endpoints")
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
    mock_assistant.bedrock_agentcore_runtime.runtime_id = "runtime-1"
    mock_assistant.bedrock_agentcore_runtime.runtime_endpoint_id = "endpoint-1"
    mock_assistant.bedrock_agentcore_runtime.runtime_endpoint_name = "Endpoint 1"
    mock_assistant.bedrock_agentcore_runtime.configuration_json = '{"message": "__QUERY_PLACEHOLDER__"}'
    mock_assistant.bedrock_agentcore_runtime.runtime_endpoint_live_version = "1"

    mock_get_by_bedrock_runtime_aws_settings_id.return_value = [mock_assistant]
    mock_list_runtime_endpoints.return_value = (endpoint_data, None)

    result, next_token = BedrockAgentCoreRuntimeService.list_importable_entities_for_main_entity(
        mock_user, "runtime-1", "setting-1", page=0, per_page=10
    )

    assert result[0]["id"] == "endpoint-1"
    assert "aiRunId" in result[0]
    assert result[0]["aiRunId"] == "assistant-1"

    assert result[1]["id"] == "endpoint-2"
    assert "aiRunId" not in result[1]


@patch(f"{_ENDPOINT_MOD}.get_setting_for_user")
@patch(f"{_ENDPOINT_MOD}.get_setting_aws_credentials")
@patch(f"{_ENDPOINT_MOD}.Assistant.get_by_bedrock_runtime_aws_settings_id")
@patch(f"{_ENDPOINT_MOD}.BedrockAgentCoreEndpointService._bedrock_list_runtime_endpoints")
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
@patch(f"{_ENDPOINT_MOD}.get_setting_for_user")
@patch(f"{_ENDPOINT_MOD}.get_setting_aws_credentials")
@patch(f"{_ENDPOINT_MOD}.BedrockAgentCoreEndpointService._bedrock_get_runtime_endpoint")
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


# --- Tests for get_importable_entity_detail (import status enrichment) ---
@patch(f"{_ENDPOINT_MOD}.get_setting_for_user")
@patch(f"{_ENDPOINT_MOD}.get_setting_aws_credentials")
@patch(f"{_ENDPOINT_MOD}.Assistant.get_by_bedrock_runtime_aws_settings_id")
@patch(f"{_ENDPOINT_MOD}.BedrockAgentCoreEndpointService._bedrock_get_runtime_endpoint")
def test_get_importable_entity_detail_includes_ai_run_id_when_imported(
    mock_get_endpoint,
    mock_get_existing,
    mock_get_aws_creds,
    mock_get_setting,
    mock_user,
    mock_setting,
    mock_aws_creds,
    endpoint_detail,
):
    """Detail response includes aiRunId and invocationJson when endpoint is already imported."""
    mock_get_setting.return_value = mock_setting
    mock_get_aws_creds.return_value = mock_aws_creds
    mock_get_endpoint.return_value = endpoint_detail

    existing_assistant = MagicMock()
    existing_assistant.id = "assistant-uuid-1"
    existing_assistant.bedrock_agentcore_runtime = MagicMock()
    existing_assistant.bedrock_agentcore_runtime.runtime_endpoint_id = "endpoint-1"
    existing_assistant.bedrock_agentcore_runtime.configuration_json = '{"prompt": "__QUERY_PLACEHOLDER__"}'
    mock_get_existing.return_value = [existing_assistant]

    result = BedrockAgentCoreRuntimeService.get_importable_entity_detail(
        user=mock_user,
        main_entity_id="runtime-1",
        importable_entity_detail="Endpoint 1",
        setting_id="setting-1",
    )

    assert result["aiRunId"] == "assistant-uuid-1"
    assert result["configurationJson"] == '{"prompt": "__QUERY_PLACEHOLDER__"}'
    assert result["id"] == "endpoint-1"
    assert result["agentRuntimeEndpointArn"] == endpoint_detail["agentRuntimeEndpointArn"]


@patch(f"{_ENDPOINT_MOD}.get_setting_for_user")
@patch(f"{_ENDPOINT_MOD}.get_setting_aws_credentials")
@patch(f"{_ENDPOINT_MOD}.Assistant.get_by_bedrock_runtime_aws_settings_id")
@patch(f"{_ENDPOINT_MOD}.BedrockAgentCoreEndpointService._bedrock_get_runtime_endpoint")
def test_get_importable_entity_detail_no_ai_run_id_when_not_imported(
    mock_get_endpoint,
    mock_get_existing,
    mock_get_aws_creds,
    mock_get_setting,
    mock_user,
    mock_setting,
    mock_aws_creds,
    endpoint_detail,
):
    """Detail response omits aiRunId when endpoint has not been imported."""
    mock_get_setting.return_value = mock_setting
    mock_get_aws_creds.return_value = mock_aws_creds
    mock_get_endpoint.return_value = endpoint_detail
    mock_get_existing.return_value = []

    result = BedrockAgentCoreRuntimeService.get_importable_entity_detail(
        user=mock_user,
        main_entity_id="runtime-1",
        importable_entity_detail="Endpoint 1",
        setting_id="setting-1",
    )

    assert "aiRunId" not in result
    assert "configurationJson" not in result


# --- Tests for import_entities ---
@patch(f"{_ENDPOINT_MOD}.get_setting_for_user")
@patch(f"{_ENDPOINT_MOD}.get_setting_aws_credentials")
@patch(f"{_ENDPOINT_MOD}.Assistant.get_by_bedrock_runtime_aws_settings_id")
@patch(f"{_ENDPOINT_MOD}.BedrockAgentCoreEndpointService._process_endpoint_import")
def test_import_entities_success(
    mock_process_endpoint_import,
    mock_get_by_bedrock_runtime_aws_settings_id,
    mock_get_setting_aws_credentials,
    mock_get_setting_for_user,
    mock_user,
):
    """Test import_entities calls _process_endpoint_import correctly."""
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
                configuration_json='{"prompt": "__QUERY_PLACEHOLDER__"}',
                setting_id="setting-1",
            ),
            ImportAgentcoreRuntime(
                id="runtime-1",
                agentcoreRuntimeEndpointName="Endpoint 2",
                configuration_json='{"text": "__QUERY_PLACEHOLDER__"}',
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
@patch(f"{_RUNTIME_MOD}.Assistant.get_by_bedrock_runtime_aws_settings_id")
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
@patch(f"{_RUNTIME_MOD}.get_setting_aws_credentials")
@patch(f"{_ENDPOINT_MOD}.BedrockAgentCoreEndpointService._bedrock_get_runtime_endpoint")
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
@patch(f"{_RUNTIME_MOD}.get_setting_aws_credentials")
@patch(f"{_ENDPOINT_MOD}.BedrockAgentCoreEndpointService._bedrock_get_runtime_endpoint")
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

    error_response = {"Error": {"Code": "ResourceNotFoundException", "Message": "Endpoint not found"}}
    mock_bedrock_get_runtime_endpoint.side_effect = ClientError(error_response, "GetRuntimeEndpoint")

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
@patch(f"{_RUNTIME_MOD}.get_setting_aws_credentials")
@patch(f"{_ENDPOINT_MOD}.BedrockAgentCoreEndpointService._bedrock_get_runtime_endpoint")
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

    error_response = {"Error": {"Code": "AccessDeniedException", "Message": "Access denied"}}
    mock_bedrock_get_runtime_endpoint.side_effect = ClientError(error_response, "GetRuntimeEndpoint")

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
    mock_assistant = MagicMock()
    mock_assistant.type = "codemie"
    mock_assistant.bedrock_agentcore_runtime = None

    result = BedrockAgentCoreRuntimeService.validate_remote_entity_exists_and_cleanup(mock_assistant)

    assert result is None
    mock_assistant.delete.assert_not_called()


def test_validate_remote_entity_exists_and_cleanup_missing_bedrock_fields():
    """Test validate_remote_entity_exists_and_cleanup with incomplete Bedrock configuration."""
    from codemie.rest_api.models.assistant import AssistantType

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
@patch(f"{_RUNTIME_MOD}.get_setting_aws_credentials")
@patch(f"{_RUNTIME_MOD}.BedrockAgentCoreRuntimeService._bedrock_invoke_runtime")
def test_invoke_agentcore_runtime_success(
    mock_bedrock_invoke_runtime,
    mock_get_setting_aws_credentials,
    mock_aws_creds,
):
    """Test invoke_agentcore_runtime successfully invokes a runtime."""
    from codemie.rest_api.models.assistant import AssistantType

    import json as _json

    mock_get_setting_aws_credentials.return_value = mock_aws_creds
    mock_stream = MagicMock()
    mock_stream.iter_lines.return_value = [b'{"text": "Test response from runtime"}']
    mock_bedrock_invoke_runtime.return_value = mock_stream

    mock_assistant = MagicMock()
    mock_assistant.type = AssistantType.BEDROCK_AGENTCORE_RUNTIME
    mock_assistant.bedrock_agentcore_runtime = MagicMock()
    mock_assistant.bedrock_agentcore_runtime.runtime_arn = "arn:aws:bedrock:us-east-1:123456789012:runtime/runtime-1"
    mock_assistant.bedrock_agentcore_runtime.runtime_endpoint_name = "Endpoint 1"
    mock_assistant.bedrock_agentcore_runtime.aws_settings_id = "setting-1"
    mock_assistant.bedrock_agentcore_runtime.configuration_json = _json.dumps(
        {"request": {"message_path": "message"}, "response": {"streaming": True, "chunk": {"text_path": "text"}}}
    )

    response = BedrockAgentCoreRuntimeService.invoke_agentcore_runtime(
        assistant=mock_assistant,
        input_text="test query",
        conversation_id="test-conversation-id",
    )

    assert response["output"] == "Test response from runtime"
    assert "time_elapsed" in response
    mock_bedrock_invoke_runtime.assert_called_once()


@patch(f"{_RUNTIME_MOD}.get_setting_aws_credentials")
@patch(f"{_RUNTIME_MOD}.BedrockAgentCoreRuntimeService._bedrock_invoke_runtime")
def test_invoke_agentcore_runtime_client_error(
    mock_bedrock_invoke_runtime,
    mock_get_setting_aws_credentials,
    mock_aws_creds,
):
    """Test invoke_agentcore_runtime handles client errors gracefully."""
    from botocore.exceptions import ClientError
    from codemie.rest_api.models.assistant import AssistantType

    mock_get_setting_aws_credentials.return_value = mock_aws_creds
    mock_bedrock_invoke_runtime.side_effect = ClientError(
        {"Error": {"Code": "400", "Message": "Bad Request"}}, "invoke_runtime"
    )

    mock_assistant = MagicMock()
    mock_assistant.type = AssistantType.BEDROCK_AGENTCORE_RUNTIME
    mock_assistant.bedrock_agentcore_runtime = MagicMock()
    mock_assistant.bedrock_agentcore_runtime.runtime_arn = "arn:aws:bedrock:us-east-1:123456789012:runtime/runtime-1"
    mock_assistant.bedrock_agentcore_runtime.runtime_endpoint_name = "Endpoint 1"
    mock_assistant.bedrock_agentcore_runtime.aws_settings_id = "setting-1"
    mock_assistant.bedrock_agentcore_runtime.configuration_json = None

    response = BedrockAgentCoreRuntimeService.invoke_agentcore_runtime(
        assistant=mock_assistant,
        input_text="test query",
        conversation_id="test-conversation-id",
    )

    assert "error" in response["output"].lower() or "clienterror" in response["output"].lower()
    assert "time_elapsed" in response


# --- Tests for _validate_configuration_json ---
def test_validate_configuration_json_valid():
    """Test _validate_configuration_json accepts a well-formed response config."""
    raw = '{"response": {"streaming": false, "body": {"text_path": "output"}}}'
    assert BedrockAgentCoreEndpointService._validate_configuration_json(raw) is None


def test_validate_configuration_json_invalid_json():
    """Test _validate_configuration_json rejects malformed JSON."""
    result = BedrockAgentCoreEndpointService._validate_configuration_json('{"prompt": "hello"')
    assert result is not None
    assert "Invalid JSON" in result


def test_validate_configuration_json_none():
    """Test _validate_configuration_json accepts None."""
    assert BedrockAgentCoreEndpointService._validate_configuration_json(None) is None


# --- Tests for _process_endpoint_import ---
@patch(f"{_ENDPOINT_MOD}.BedrockAgentCoreEndpointService._validate_configuration_json")
@patch(f"{_ENDPOINT_MOD}.BedrockAgentCoreEndpointService._bedrock_get_runtime_endpoint")
@patch(f"{_ENDPOINT_MOD}.BedrockAgentCoreEndpointService._create_assistant_data")
@patch(f"{_ENDPOINT_MOD}.BedrockAgentCoreEndpointService._create_or_update_entity")
def test_process_endpoint_import_success(
    mock_create_or_update_entity,
    mock_create_assistant_data,
    mock_bedrock_get_runtime_endpoint,
    mock_validate_configuration_json,
    mock_user,
    mock_setting,
    mock_aws_creds,
):
    """Test _process_endpoint_import successfully imports an endpoint."""
    mock_validate_configuration_json.return_value = None
    mock_bedrock_get_runtime_endpoint.return_value = {
        "id": "endpoint-1",
        "agentRuntimeEndpointArn": "arn:aws:bedrock:us-east-1:123456789012:runtime-endpoint/endpoint-1",
        "status": "READY",
        "name": "Endpoint 1",
    }
    mock_create_assistant_data.return_value = {"name": "Test Assistant"}
    mock_create_or_update_entity.return_value = "assistant-1"

    result = BedrockAgentCoreEndpointService._process_endpoint_import(
        user=mock_user,
        setting=mock_setting,
        aws_creds=mock_aws_creds,
        existing_entities_map={},
        input_runtime_id="runtime-1",
        input_endpoint_name="Endpoint 1",
        configuration_json='{"prompt": "__QUERY_PLACEHOLDER__"}',
    )

    assert result["runtimeId"] == "runtime-1"
    assert result["endpointName"] == "Endpoint 1"
    assert result["aiRunId"] == "assistant-1"
    assert "error" not in result


@patch(f"{_ENDPOINT_MOD}.BedrockAgentCoreEndpointService._validate_configuration_json")
def test_process_endpoint_import_invalid_json(
    mock_validate_configuration_json,
    mock_user,
    mock_setting,
    mock_aws_creds,
):
    """Test _process_endpoint_import raises AgentcoreEndpointValidationError for invalid JSON."""
    from codemie.service.aws_bedrock.exceptions import AgentcoreEndpointValidationError

    mock_validate_configuration_json.return_value = "Invalid JSON template"

    with pytest.raises(AgentcoreEndpointValidationError):
        BedrockAgentCoreEndpointService._process_endpoint_import(
            user=mock_user,
            setting=mock_setting,
            aws_creds=mock_aws_creds,
            existing_entities_map={},
            input_runtime_id="runtime-1",
            input_endpoint_name="Endpoint 1",
            configuration_json='{"prompt": "no placeholder"}',
        )


@patch(f"{_ENDPOINT_MOD}.BedrockAgentCoreEndpointService._validate_configuration_json")
@patch(f"{_ENDPOINT_MOD}.BedrockAgentCoreEndpointService._bedrock_get_runtime_endpoint")
def test_process_endpoint_import_endpoint_not_ready(
    mock_bedrock_get_runtime_endpoint,
    mock_validate_configuration_json,
    mock_user,
    mock_setting,
    mock_aws_creds,
):
    """Test _process_endpoint_import raises ExtendedHTTPException when endpoint is not in READY status."""
    mock_validate_configuration_json.return_value = None
    mock_bedrock_get_runtime_endpoint.return_value = {
        "id": "endpoint-1",
        "agentRuntimeEndpointArn": "arn:aws:bedrock:us-east-1:123456789012:runtime-endpoint/endpoint-1",
        "status": "NOT_READY",
        "name": "Endpoint 1",
    }

    with pytest.raises(ExtendedHTTPException) as exc_info:
        BedrockAgentCoreEndpointService._process_endpoint_import(
            user=mock_user,
            setting=mock_setting,
            aws_creds=mock_aws_creds,
            existing_entities_map={},
            input_runtime_id="runtime-1",
            input_endpoint_name="Endpoint 1",
            configuration_json='{"prompt": "__QUERY_PLACEHOLDER__"}',
        )

    assert exc_info.value.code == 409
    assert "not in READY status" in exc_info.value.message


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

    result = BedrockAgentCoreEndpointService._create_assistant_data(
        user=mock_user,
        setting=mock_setting,
        input_runtime_id="runtime-1",
        endpoint_info=endpoint_info,
        configuration_json='{"prompt": "__QUERY_PLACEHOLDER__"}',
    )

    assert result["name"] == "runtime-1:Endpoint 1"
    assert result["description"] == "Test endpoint"
    assert "bedrock_agentcore_runtime" in result
    assert result["bedrock_agentcore_runtime"]["runtime_id"] == "runtime-1"
    assert result["bedrock_agentcore_runtime"]["runtime_endpoint_name"] == "Endpoint 1"
    assert result["bedrock_agentcore_runtime"]["configuration_json"] == '{"prompt": "__QUERY_PLACEHOLDER__"}'
    assert result["type"] == "bedrock_agentcore_runtime"


def test_create_assistant_data_with_custom_name_and_description(mock_user, mock_setting):
    """Test _create_assistant_data uses assistant_name and assistant_description when provided."""
    endpoint_info = {
        "id": "endpoint-1",
        "name": "Endpoint 1",
        "description": "Test endpoint",
        "agentRuntimeArn": "arn:aws:bedrock:us-east-1:123456789012:runtime/runtime-1",
        "agentRuntimeEndpointArn": "arn:aws:bedrock:us-east-1:123456789012:runtime-endpoint/endpoint-1",
        "liveVersion": "1",
    }

    result = BedrockAgentCoreEndpointService._create_assistant_data(
        user=mock_user,
        setting=mock_setting,
        input_runtime_id="runtime-1",
        endpoint_info=endpoint_info,
        configuration_json='{"prompt": "__QUERY_PLACEHOLDER__"}',
        assistant_name="My Custom Assistant",
        assistant_description="My custom description",
    )

    assert result["name"] == "My Custom Assistant"
    assert result["description"] == "My custom description"
    assert result["slug"].startswith("My Custom Assistant-")


# --- Tests for list_importable_entities_for_main_entity (invocationJson enrichment) ---
@patch(f"{_ENDPOINT_MOD}.get_setting_for_user")
@patch(f"{_ENDPOINT_MOD}.get_setting_aws_credentials")
@patch(f"{_ENDPOINT_MOD}.Assistant.get_by_bedrock_runtime_aws_settings_id")
@patch(f"{_ENDPOINT_MOD}.BedrockAgentCoreEndpointService._bedrock_list_runtime_endpoints")
def test_list_importable_entities_includes_configuration_json_for_imported_endpoint(
    mock_list_endpoints,
    mock_get_existing,
    mock_get_aws_creds,
    mock_get_setting,
    mock_user,
    mock_setting,
    mock_aws_creds,
    endpoint_data,
):
    """Already-imported endpoint items include invocationJson alongside aiRunId."""
    mock_get_setting.return_value = mock_setting
    mock_get_aws_creds.return_value = mock_aws_creds
    mock_list_endpoints.return_value = (endpoint_data, None)

    existing_assistant = MagicMock()
    existing_assistant.id = "assistant-uuid-1"
    existing_assistant.bedrock_agentcore_runtime = MagicMock()
    existing_assistant.bedrock_agentcore_runtime.runtime_id = "runtime-1"
    existing_assistant.bedrock_agentcore_runtime.runtime_endpoint_id = "endpoint-1"
    existing_assistant.bedrock_agentcore_runtime.configuration_json = '{"message": "__QUERY_PLACEHOLDER__"}'
    mock_get_existing.return_value = [existing_assistant]

    result, _ = BedrockAgentCoreRuntimeService.list_importable_entities_for_main_entity(
        user=mock_user,
        main_entity_id="runtime-1",
        setting_id="setting-1",
        page=0,
        per_page=10,
    )

    imported = next(r for r in result if r["id"] == "endpoint-1")
    assert imported["aiRunId"] == "assistant-uuid-1"
    assert imported["configurationJson"] == '{"message": "__QUERY_PLACEHOLDER__"}'

    not_imported = next(r for r in result if r["id"] == "endpoint-2")
    assert "aiRunId" not in not_imported
    assert "configurationJson" not in not_imported


# --- Tests for unimport_entity ---
@patch(f"{_ENDPOINT_MOD}.GuardrailService.remove_guardrail_assignments_for_entity")
@patch(f"{_ENDPOINT_MOD}.Ability")
@patch(f"{_ENDPOINT_MOD}.Assistant.find_by_id")
def test_unimport_entity_agentcore_deletes_and_removes_guardrails(
    mock_find_by_id,
    mock_ability_cls,
    mock_remove_guardrails,
    mock_user,
):
    """unimport_entity deletes the assistant and removes guardrail assignments."""
    mock_assistant = MagicMock()
    mock_assistant.id = "assistant-uuid-1"
    mock_find_by_id.return_value = mock_assistant
    mock_ability_cls.return_value.can.return_value = True

    BedrockAgentCoreRuntimeService.unimport_entity("assistant-uuid-1", mock_user)

    mock_assistant.delete.assert_called_once()
    mock_remove_guardrails.assert_called_once_with(GuardrailEntity.ASSISTANT, "assistant-uuid-1")


@patch(f"{_ENDPOINT_MOD}.Assistant.find_by_id")
def test_unimport_entity_agentcore_raises_404_when_not_found(mock_find_by_id, mock_user):
    """unimport_entity raises HTTP 404 when entity does not exist."""
    mock_find_by_id.return_value = None

    with pytest.raises(EntityNotFound):
        BedrockAgentCoreRuntimeService.unimport_entity("missing-id", mock_user)


@patch(f"{_ENDPOINT_MOD}.Ability")
@patch(f"{_ENDPOINT_MOD}.Assistant.find_by_id")
def test_unimport_entity_agentcore_raises_403_when_no_permission(mock_find_by_id, mock_ability_cls, mock_user):
    """unimport_entity raises EntityAccessDenied when user lacks DELETE permission."""
    mock_find_by_id.return_value = MagicMock()
    mock_ability_cls.return_value.can.return_value = False

    with pytest.raises(EntityAccessDenied):
        BedrockAgentCoreRuntimeService.unimport_entity("assistant-uuid-1", mock_user)


# --- Tests for _create_or_update_entity ---
@patch(f"{_ENDPOINT_MOD}.ensure_application_exists")
@patch(f"{_ENDPOINT_MOD}.Assistant")
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

    result = BedrockAgentCoreEndpointService._create_or_update_entity(
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

    result = BedrockAgentCoreEndpointService._create_or_update_entity(
        endpoint_id="endpoint-1",
        assistant_data=assistant_data,
        existing_entities_map=existing_entities_map,
        runtime_id="runtime-1",
    )

    assert result == "assistant-1"
    assert mock_assistant.name == "Updated Assistant"
    mock_assistant.save.assert_called_once()


# --- Tests for is_resource_not_found ---
def test_is_resource_not_found_returns_true():
    """Test is_resource_not_found returns True for ResourceNotFoundException."""
    from botocore.exceptions import ClientError
    from codemie.service.aws_bedrock.exceptions import is_resource_not_found

    e = ClientError({"Error": {"Code": "ResourceNotFoundException", "Message": "Not found"}}, "OperationName")
    assert is_resource_not_found(e) is True


def test_is_resource_not_found_returns_false_for_other_code():
    """Test is_resource_not_found returns False for non-ResourceNotFoundException codes."""
    from botocore.exceptions import ClientError
    from codemie.service.aws_bedrock.exceptions import is_resource_not_found

    e = ClientError({"Error": {"Code": "AccessDeniedException", "Message": "Denied"}}, "OperationName")
    assert is_resource_not_found(e) is False


def test_is_resource_not_found_case_insensitive():
    """Test is_resource_not_found is case-insensitive."""
    from botocore.exceptions import ClientError
    from codemie.service.aws_bedrock.exceptions import is_resource_not_found

    e = ClientError({"Error": {"Code": "resourcenotfoundexception", "Message": "Not found"}}, "OperationName")
    assert is_resource_not_found(e) is True


def test_is_resource_not_found_strips_whitespace():
    """Test is_resource_not_found strips whitespace from the error code."""
    from botocore.exceptions import ClientError
    from codemie.service.aws_bedrock.exceptions import is_resource_not_found

    e = ClientError({"Error": {"Code": " ResourceNotFoundException ", "Message": "Not found"}}, "OperationName")
    assert is_resource_not_found(e) is True


# --- New tests for list_main_entities (DELETED_ON_AWS) ---
@patch(f"{_RUNTIME_MOD}.BedrockAgentCoreRuntimeService._get_deleted_runtime_entities")
@patch(f"{_RUNTIME_MOD}.get_setting_for_user")
@patch(f"{_RUNTIME_MOD}.get_setting_aws_credentials")
@patch(f"{_RUNTIME_MOD}.BedrockAgentCoreRuntimeService._bedrock_list_agent_runtimes")
def test_list_main_entities_appends_deleted_on_aws(
    mock_bedrock_list_agent_runtimes,
    mock_get_setting_aws_credentials,
    mock_get_setting_for_user,
    mock_get_deleted_runtime_entities,
    mock_user,
    mock_setting,
    mock_aws_creds,
    runtime_data,
):
    """Test list_main_entities appends DELETED_ON_AWS runtimes from _get_deleted_runtime_entities."""
    from codemie.service.aws_bedrock.bedrock_agentcore_runtime_service import AgentcoreRuntimeEntity, RuntimeStatus

    mock_get_setting_for_user.return_value = mock_setting
    mock_get_setting_aws_credentials.return_value = mock_aws_creds
    mock_bedrock_list_agent_runtimes.return_value = (runtime_data, None)
    mock_get_deleted_runtime_entities.return_value = [
        AgentcoreRuntimeEntity(id="deleted-runtime", status=RuntimeStatus.DELETED_ON_AWS)
    ]

    result, next_token = BedrockAgentCoreRuntimeService.list_main_entities(mock_user, "setting-1", page=0, per_page=10)

    assert len(result) == 3
    deleted = next(r for r in result if r.id == "deleted-runtime")
    assert deleted.status == RuntimeStatus.DELETED_ON_AWS
    mock_get_deleted_runtime_entities.assert_called_once()


# --- New tests for get_main_entity_detail (DELETED_ON_AWS) ---
@patch(f"{_RUNTIME_MOD}.Assistant.get_by_bedrock_runtime_aws_settings_id")
@patch(f"{_RUNTIME_MOD}.get_setting_for_user")
@patch(f"{_RUNTIME_MOD}.get_setting_aws_credentials")
@patch(f"{_RUNTIME_MOD}.BedrockAgentCoreRuntimeService._bedrock_get_agent_runtime")
def test_get_main_entity_detail_returns_deleted_on_aws_when_imported(
    mock_bedrock_get_agent_runtime,
    mock_get_setting_aws_credentials,
    mock_get_setting_for_user,
    mock_get_by_runtime,
    mock_user,
    mock_setting,
    mock_aws_creds,
):
    """Test get_main_entity_detail returns DELETED_ON_AWS when runtime deleted but has imported assistants."""
    from botocore.exceptions import ClientError
    from codemie.service.aws_bedrock.bedrock_agentcore_runtime_service import RuntimeStatus

    mock_get_setting_for_user.return_value = mock_setting
    mock_get_setting_aws_credentials.return_value = mock_aws_creds
    error_response = {"Error": {"Code": "ResourceNotFoundException", "Message": "Not found"}}
    mock_bedrock_get_agent_runtime.side_effect = ClientError(error_response, "GetAgentRuntime")

    mock_assistant = MagicMock()
    mock_assistant.bedrock_agentcore_runtime = MagicMock()
    mock_assistant.bedrock_agentcore_runtime.runtime_id = "runtime-1"
    mock_get_by_runtime.return_value = [mock_assistant]

    result = BedrockAgentCoreRuntimeService.get_main_entity_detail(mock_user, "runtime-1", "setting-1")

    assert result.id == "runtime-1"
    assert result.status == RuntimeStatus.DELETED_ON_AWS


@patch(f"{_RUNTIME_MOD}.Assistant.get_by_bedrock_runtime_aws_settings_id")
@patch(f"{_RUNTIME_MOD}.get_setting_for_user")
@patch(f"{_RUNTIME_MOD}.get_setting_aws_credentials")
@patch(f"{_RUNTIME_MOD}.BedrockAgentCoreRuntimeService._bedrock_get_agent_runtime")
def test_get_main_entity_detail_reraises_when_deleted_no_imported(
    mock_bedrock_get_agent_runtime,
    mock_get_setting_aws_credentials,
    mock_get_setting_for_user,
    mock_get_by_runtime,
    mock_user,
    mock_setting,
    mock_aws_creds,
):
    """Test get_main_entity_detail re-raises when runtime deleted and no imported assistants remain."""
    from botocore.exceptions import ClientError

    mock_get_setting_for_user.return_value = mock_setting
    mock_get_setting_aws_credentials.return_value = mock_aws_creds
    error_response = {"Error": {"Code": "ResourceNotFoundException", "Message": "Not found"}}
    mock_bedrock_get_agent_runtime.side_effect = ClientError(error_response, "GetAgentRuntime")
    mock_get_by_runtime.return_value = []

    with pytest.raises(ExtendedHTTPException):
        BedrockAgentCoreRuntimeService.get_main_entity_detail(mock_user, "runtime-1", "setting-1")


@patch(f"{_RUNTIME_MOD}.Assistant.get_by_bedrock_runtime_aws_settings_id")
@patch(f"{_RUNTIME_MOD}.get_setting_for_user")
@patch(f"{_RUNTIME_MOD}.get_setting_aws_credentials")
@patch(f"{_RUNTIME_MOD}.BedrockAgentCoreRuntimeService._bedrock_get_agent_runtime")
def test_get_main_entity_detail_deleted_runtime_no_match_for_this_runtime(
    mock_bedrock_get_agent_runtime,
    mock_get_setting_aws_credentials,
    mock_get_setting_for_user,
    mock_get_by_runtime,
    mock_user,
    mock_setting,
    mock_aws_creds,
):
    """Test get_main_entity_detail re-raises when assistants exist but belong to a different runtime."""
    from botocore.exceptions import ClientError

    mock_get_setting_for_user.return_value = mock_setting
    mock_get_setting_aws_credentials.return_value = mock_aws_creds
    error_response = {"Error": {"Code": "ResourceNotFoundException", "Message": "Not found"}}
    mock_bedrock_get_agent_runtime.side_effect = ClientError(error_response, "GetAgentRuntime")

    mock_assistant = MagicMock()
    mock_assistant.bedrock_agentcore_runtime = MagicMock()
    mock_assistant.bedrock_agentcore_runtime.runtime_id = "different-runtime-id"
    mock_get_by_runtime.return_value = [mock_assistant]

    with pytest.raises(ExtendedHTTPException):
        BedrockAgentCoreRuntimeService.get_main_entity_detail(mock_user, "runtime-1", "setting-1")


# --- Tests for _get_deleted_runtime_entities ---
@patch(f"{_RUNTIME_MOD}.BedrockAgentCoreRuntimeService._bedrock_get_agent_runtime")
@patch(f"{_RUNTIME_MOD}.Assistant.get_by_bedrock_runtime_aws_settings_id")
def test_get_deleted_runtime_entities_returns_empty_when_no_candidates(
    mock_get_by_runtime,
    mock_bedrock_get_agent_runtime,
    mock_aws_creds,
):
    """Test _get_deleted_runtime_entities returns empty list when no imported runtimes."""
    mock_get_by_runtime.return_value = []

    result = BedrockAgentCoreRuntimeService._get_deleted_runtime_entities(
        setting_id="setting-1",
        seen_runtime_ids=set(),
        aws_creds=mock_aws_creds,
    )

    assert result == []
    mock_bedrock_get_agent_runtime.assert_not_called()


@patch(f"{_RUNTIME_MOD}.BedrockAgentCoreRuntimeService._bedrock_get_agent_runtime")
@patch(f"{_RUNTIME_MOD}.Assistant.get_by_bedrock_runtime_aws_settings_id")
def test_get_deleted_runtime_entities_excludes_seen_ids(
    mock_get_by_runtime,
    mock_bedrock_get_agent_runtime,
    mock_aws_creds,
):
    """Test _get_deleted_runtime_entities does not check already-seen runtimes."""
    mock_assistant = MagicMock()
    mock_assistant.bedrock_agentcore_runtime = MagicMock()
    mock_assistant.bedrock_agentcore_runtime.runtime_id = "runtime-1"
    mock_get_by_runtime.return_value = [mock_assistant]

    result = BedrockAgentCoreRuntimeService._get_deleted_runtime_entities(
        setting_id="setting-1",
        seen_runtime_ids={"runtime-1"},
        aws_creds=mock_aws_creds,
    )

    assert result == []
    mock_bedrock_get_agent_runtime.assert_not_called()


@patch(f"{_RUNTIME_MOD}.BedrockAgentCoreRuntimeService._bedrock_get_agent_runtime")
@patch(f"{_RUNTIME_MOD}.Assistant.get_by_bedrock_runtime_aws_settings_id")
def test_get_deleted_runtime_entities_returns_deleted_on_resource_not_found(
    mock_get_by_runtime,
    mock_bedrock_get_agent_runtime,
    mock_aws_creds,
):
    """Test _get_deleted_runtime_entities appends DELETED_ON_AWS for ResourceNotFoundException."""
    from botocore.exceptions import ClientError
    from codemie.service.aws_bedrock.bedrock_agentcore_runtime_service import RuntimeStatus

    mock_assistant = MagicMock()
    mock_assistant.bedrock_agentcore_runtime = MagicMock()
    mock_assistant.bedrock_agentcore_runtime.runtime_id = "runtime-deleted"
    mock_get_by_runtime.return_value = [mock_assistant]

    error_response = {"Error": {"Code": "ResourceNotFoundException", "Message": "Not found"}}
    mock_bedrock_get_agent_runtime.side_effect = ClientError(error_response, "GetAgentRuntime")

    result = BedrockAgentCoreRuntimeService._get_deleted_runtime_entities(
        setting_id="setting-1",
        seen_runtime_ids=set(),
        aws_creds=mock_aws_creds,
    )

    assert len(result) == 1
    assert result[0].id == "runtime-deleted"
    assert result[0].status == RuntimeStatus.DELETED_ON_AWS


@patch(f"{_RUNTIME_MOD}.BedrockAgentCoreRuntimeService._bedrock_get_agent_runtime")
@patch(f"{_RUNTIME_MOD}.Assistant.get_by_bedrock_runtime_aws_settings_id")
def test_get_deleted_runtime_entities_skips_on_other_client_error(
    mock_get_by_runtime,
    mock_bedrock_get_agent_runtime,
    mock_aws_creds,
):
    """Test _get_deleted_runtime_entities skips runtime on non-ResourceNotFoundException ClientError."""
    from botocore.exceptions import ClientError

    mock_assistant = MagicMock()
    mock_assistant.bedrock_agentcore_runtime = MagicMock()
    mock_assistant.bedrock_agentcore_runtime.runtime_id = "runtime-access-denied"
    mock_get_by_runtime.return_value = [mock_assistant]

    error_response = {"Error": {"Code": "AccessDeniedException", "Message": "Access denied"}}
    mock_bedrock_get_agent_runtime.side_effect = ClientError(error_response, "GetAgentRuntime")

    result = BedrockAgentCoreRuntimeService._get_deleted_runtime_entities(
        setting_id="setting-1",
        seen_runtime_ids=set(),
        aws_creds=mock_aws_creds,
    )

    assert result == []


@patch(f"{_RUNTIME_MOD}.BedrockAgentCoreRuntimeService._bedrock_get_agent_runtime")
@patch(f"{_RUNTIME_MOD}.Assistant.get_by_bedrock_runtime_aws_settings_id")
def test_get_deleted_runtime_entities_skips_on_unexpected_error(
    mock_get_by_runtime,
    mock_bedrock_get_agent_runtime,
    mock_aws_creds,
):
    """Test _get_deleted_runtime_entities skips runtime on unexpected errors."""
    mock_assistant = MagicMock()
    mock_assistant.bedrock_agentcore_runtime = MagicMock()
    mock_assistant.bedrock_agentcore_runtime.runtime_id = "runtime-error"
    mock_get_by_runtime.return_value = [mock_assistant]

    mock_bedrock_get_agent_runtime.side_effect = Exception("Unexpected error")

    result = BedrockAgentCoreRuntimeService._get_deleted_runtime_entities(
        setting_id="setting-1",
        seen_runtime_ids=set(),
        aws_creds=mock_aws_creds,
    )

    assert result == []


# --- Tests for _fetch_main_entity_names_for_setting ---
@patch(f"{_RUNTIME_MOD}.BedrockAgentCoreRuntimeService._get_deleted_runtime_entities")
@patch(f"{_RUNTIME_MOD}.BedrockAgentCoreRuntimeService._bedrock_list_agent_runtimes")
@patch(f"{_RUNTIME_MOD}.get_setting_aws_credentials")
def test_fetch_main_entity_names_includes_deleted_runtime_ids(
    mock_get_setting_aws_credentials,
    mock_bedrock_list_agent_runtimes,
    mock_get_deleted_runtime_entities,
    mock_setting,
    mock_aws_creds,
):
    """Test _fetch_main_entity_names_for_setting appends deleted runtime IDs to the entity names list."""
    from codemie.service.aws_bedrock.bedrock_agentcore_runtime_service import AgentcoreRuntimeEntity, RuntimeStatus

    mock_get_setting_aws_credentials.return_value = mock_aws_creds
    mock_bedrock_list_agent_runtimes.return_value = (
        [{"agentRuntimeId": "runtime-1", "agentRuntimeName": "Runtime 1"}],
        None,
    )
    mock_get_deleted_runtime_entities.return_value = [
        AgentcoreRuntimeEntity(id="deleted-runtime-id", status=RuntimeStatus.DELETED_ON_AWS)
    ]

    names, has_deleted = BedrockAgentCoreRuntimeService._fetch_main_entity_names_for_setting(mock_setting)

    assert "Runtime 1" in names
    assert "deleted-runtime-id" in names


@patch(f"{_RUNTIME_MOD}.BedrockAgentCoreRuntimeService._get_deleted_runtime_entities")
@patch(f"{_RUNTIME_MOD}.BedrockAgentCoreRuntimeService._bedrock_list_agent_runtimes")
@patch(f"{_RUNTIME_MOD}.get_setting_aws_credentials")
def test_fetch_main_entity_names_respects_count_limit_with_deleted(
    mock_get_setting_aws_credentials,
    mock_bedrock_list_agent_runtimes,
    mock_get_deleted_runtime_entities,
    mock_setting,
    mock_aws_creds,
):
    """Test _fetch_main_entity_names_for_setting does not exceed ALL_SETTINGS_OVERVIEW_ENTITY_COUNT."""
    from codemie.service.aws_bedrock.bedrock_agentcore_runtime_service import AgentcoreRuntimeEntity, RuntimeStatus
    from codemie.service.aws_bedrock.base_bedrock_service import ALL_SETTINGS_OVERVIEW_ENTITY_COUNT

    mock_get_setting_aws_credentials.return_value = mock_aws_creds
    aws_runtimes = [
        {"agentRuntimeId": f"rt-{i}", "agentRuntimeName": f"Runtime {i}"}
        for i in range(ALL_SETTINGS_OVERVIEW_ENTITY_COUNT)
    ]
    mock_bedrock_list_agent_runtimes.return_value = (aws_runtimes, None)
    mock_get_deleted_runtime_entities.return_value = [
        AgentcoreRuntimeEntity(id="deleted-runtime-id", status=RuntimeStatus.DELETED_ON_AWS)
    ]

    names, has_deleted = BedrockAgentCoreRuntimeService._fetch_main_entity_names_for_setting(mock_setting)

    assert len(names) == ALL_SETTINGS_OVERVIEW_ENTITY_COUNT
    assert "deleted-runtime-id" not in names


# --- Tests for list_importable_entities_for_main_entity (cross-runtime fix) ---
@patch(f"{_ENDPOINT_MOD}.BedrockAgentCoreEndpointService._get_deleted_endpoint_entities")
@patch(f"{_ENDPOINT_MOD}.get_setting_for_user")
@patch(f"{_ENDPOINT_MOD}.get_setting_aws_credentials")
@patch(f"{_ENDPOINT_MOD}.Assistant.get_by_bedrock_runtime_aws_settings_id")
@patch(f"{_ENDPOINT_MOD}.BedrockAgentCoreEndpointService._bedrock_list_runtime_endpoints")
def test_list_importable_entities_excludes_endpoints_from_other_runtimes(
    mock_list_endpoints,
    mock_get_by_setting,
    mock_get_aws_creds,
    mock_get_setting,
    mock_get_deleted_endpoints,
    mock_user,
    mock_setting,
    mock_aws_creds,
    endpoint_data,
):
    """Test that only endpoints belonging to the requested runtime_id are matched to imported assistants."""
    mock_get_setting.return_value = mock_setting
    mock_get_aws_creds.return_value = mock_aws_creds
    mock_list_endpoints.return_value = (endpoint_data, None)
    mock_get_deleted_endpoints.return_value = []

    assistant_same_runtime = MagicMock()
    assistant_same_runtime.id = "assistant-1"
    assistant_same_runtime.bedrock_agentcore_runtime = MagicMock()
    assistant_same_runtime.bedrock_agentcore_runtime.runtime_id = "runtime-1"
    assistant_same_runtime.bedrock_agentcore_runtime.runtime_endpoint_id = "endpoint-1"
    assistant_same_runtime.bedrock_agentcore_runtime.runtime_endpoint_name = "Endpoint 1"
    assistant_same_runtime.bedrock_agentcore_runtime.runtime_endpoint_live_version = "1"
    assistant_same_runtime.bedrock_agentcore_runtime.configuration_json = None

    assistant_other_runtime = MagicMock()
    assistant_other_runtime.id = "assistant-2"
    assistant_other_runtime.bedrock_agentcore_runtime = MagicMock()
    assistant_other_runtime.bedrock_agentcore_runtime.runtime_id = "runtime-2"
    assistant_other_runtime.bedrock_agentcore_runtime.runtime_endpoint_id = "endpoint-2"
    assistant_other_runtime.bedrock_agentcore_runtime.runtime_endpoint_name = "Endpoint 2"
    assistant_other_runtime.bedrock_agentcore_runtime.runtime_endpoint_live_version = "2"
    assistant_other_runtime.bedrock_agentcore_runtime.configuration_json = None

    mock_get_by_setting.return_value = [assistant_same_runtime, assistant_other_runtime]

    result, _ = BedrockAgentCoreRuntimeService.list_importable_entities_for_main_entity(
        user=mock_user,
        main_entity_id="runtime-1",
        setting_id="setting-1",
        page=0,
        per_page=10,
    )

    ep1 = next(r for r in result if r["id"] == "endpoint-1")
    assert ep1["aiRunId"] == "assistant-1"

    ep2 = next(r for r in result if r["id"] == "endpoint-2")
    assert ep2.aiRunId is None


@patch(f"{_ENDPOINT_MOD}.BedrockAgentCoreEndpointService._get_deleted_endpoint_entities")
@patch(f"{_ENDPOINT_MOD}.get_setting_for_user")
@patch(f"{_ENDPOINT_MOD}.get_setting_aws_credentials")
@patch(f"{_ENDPOINT_MOD}.Assistant.get_by_bedrock_runtime_aws_settings_id")
@patch(f"{_ENDPOINT_MOD}.BedrockAgentCoreEndpointService._bedrock_list_runtime_endpoints")
def test_list_importable_entities_appends_deleted_on_aws_endpoints(
    mock_list_endpoints,
    mock_get_by_setting,
    mock_get_aws_creds,
    mock_get_setting,
    mock_get_deleted_endpoints,
    mock_user,
    mock_setting,
    mock_aws_creds,
    endpoint_data,
):
    """Test list_importable_entities_for_main_entity appends DELETED_ON_AWS endpoints."""
    from codemie.service.aws_bedrock.agentcore.bedrock_agentcore_endpoint_service import (
        AgentcoreEndpointEntity,
        EndpointStatus,
    )

    mock_get_setting.return_value = mock_setting
    mock_get_aws_creds.return_value = mock_aws_creds
    mock_get_by_setting.return_value = []
    mock_list_endpoints.return_value = (endpoint_data, None)
    mock_get_deleted_endpoints.return_value = [
        AgentcoreEndpointEntity(id="deleted-ep", status=EndpointStatus.DELETED_ON_AWS, aiRunId="assistant-99")
    ]

    result, _ = BedrockAgentCoreRuntimeService.list_importable_entities_for_main_entity(
        user=mock_user,
        main_entity_id="runtime-1",
        setting_id="setting-1",
        page=0,
        per_page=10,
    )

    assert len(result) == 3
    deleted = next(r for r in result if r["id"] == "deleted-ep")
    assert deleted["status"] == EndpointStatus.DELETED_ON_AWS
    assert deleted["aiRunId"] == "assistant-99"


# --- Tests for _get_deleted_endpoint_entities ---
@patch(f"{_ENDPOINT_MOD}.BedrockAgentCoreEndpointService._bedrock_get_runtime_endpoint")
def test_get_deleted_endpoint_entities_returns_empty_when_no_candidates(mock_get_endpoint, mock_aws_creds):
    """Test _get_deleted_endpoint_entities returns empty list when existing_entities_map is empty."""
    result = BedrockAgentCoreEndpointService._get_deleted_endpoint_entities(
        runtime_id="runtime-1",
        existing_entities_map={},
        seen_endpoint_ids=set(),
        aws_creds=mock_aws_creds,
    )

    assert result == []
    mock_get_endpoint.assert_not_called()


@patch(f"{_ENDPOINT_MOD}.BedrockAgentCoreEndpointService._bedrock_get_runtime_endpoint")
def test_get_deleted_endpoint_entities_skips_seen_endpoint_ids(mock_get_endpoint, mock_aws_creds):
    """Test _get_deleted_endpoint_entities does not check endpoints already seen from AWS."""
    mock_assistant = MagicMock()
    mock_assistant.id = "assistant-1"
    mock_assistant.bedrock_agentcore_runtime = MagicMock()
    mock_assistant.bedrock_agentcore_runtime.runtime_endpoint_name = "Endpoint 1"

    result = BedrockAgentCoreEndpointService._get_deleted_endpoint_entities(
        runtime_id="runtime-1",
        existing_entities_map={"endpoint-1": mock_assistant},
        seen_endpoint_ids={"endpoint-1"},
        aws_creds=mock_aws_creds,
    )

    assert result == []
    mock_get_endpoint.assert_not_called()


@patch(f"{_ENDPOINT_MOD}.BedrockAgentCoreEndpointService._bedrock_get_runtime_endpoint")
def test_get_deleted_endpoint_entities_returns_deleted_on_resource_not_found(mock_get_endpoint, mock_aws_creds):
    """Test _get_deleted_endpoint_entities appends DELETED_ON_AWS for ResourceNotFoundException."""
    from botocore.exceptions import ClientError
    from codemie.service.aws_bedrock.agentcore.bedrock_agentcore_endpoint_service import EndpointStatus

    mock_assistant = MagicMock()
    mock_assistant.id = "assistant-1"
    mock_assistant.bedrock_agentcore_runtime = MagicMock()
    mock_assistant.bedrock_agentcore_runtime.runtime_endpoint_name = "Deleted Endpoint"
    mock_assistant.bedrock_agentcore_runtime.runtime_endpoint_id = "endpoint-deleted"
    mock_assistant.bedrock_agentcore_runtime.runtime_endpoint_description = "desc"
    mock_assistant.bedrock_agentcore_runtime.runtime_endpoint_live_version = "1"
    mock_assistant.bedrock_agentcore_runtime.configuration_json = None

    error_response = {"Error": {"Code": "ResourceNotFoundException", "Message": "Not found"}}
    mock_get_endpoint.side_effect = ClientError(error_response, "GetRuntimeEndpoint")

    result = BedrockAgentCoreEndpointService._get_deleted_endpoint_entities(
        runtime_id="runtime-1",
        existing_entities_map={"endpoint-deleted": mock_assistant},
        seen_endpoint_ids=set(),
        aws_creds=mock_aws_creds,
    )

    assert len(result) == 1
    assert result[0].id == "endpoint-deleted"
    assert result[0].status == EndpointStatus.DELETED_ON_AWS
    assert result[0].aiRunId == "assistant-1"


@patch(f"{_ENDPOINT_MOD}.BedrockAgentCoreEndpointService._bedrock_get_runtime_endpoint")
def test_get_deleted_endpoint_entities_skips_on_other_client_error(mock_get_endpoint, mock_aws_creds):
    """Test _get_deleted_endpoint_entities skips endpoint on non-ResourceNotFoundException ClientError."""
    from botocore.exceptions import ClientError

    mock_assistant = MagicMock()
    mock_assistant.id = "assistant-1"
    mock_assistant.bedrock_agentcore_runtime = MagicMock()
    mock_assistant.bedrock_agentcore_runtime.runtime_endpoint_name = "Endpoint 1"

    error_response = {"Error": {"Code": "AccessDeniedException", "Message": "Access denied"}}
    mock_get_endpoint.side_effect = ClientError(error_response, "GetRuntimeEndpoint")

    result = BedrockAgentCoreEndpointService._get_deleted_endpoint_entities(
        runtime_id="runtime-1",
        existing_entities_map={"endpoint-1": mock_assistant},
        seen_endpoint_ids=set(),
        aws_creds=mock_aws_creds,
    )

    assert result == []


@patch(f"{_ENDPOINT_MOD}.BedrockAgentCoreEndpointService._bedrock_get_runtime_endpoint")
def test_get_deleted_endpoint_entities_skips_on_unexpected_error(mock_get_endpoint, mock_aws_creds):
    """Test _get_deleted_endpoint_entities skips endpoint on unexpected errors."""
    mock_assistant = MagicMock()
    mock_assistant.id = "assistant-1"
    mock_assistant.bedrock_agentcore_runtime = MagicMock()
    mock_assistant.bedrock_agentcore_runtime.runtime_endpoint_name = "Endpoint 1"

    mock_get_endpoint.side_effect = Exception("Unexpected error")

    result = BedrockAgentCoreEndpointService._get_deleted_endpoint_entities(
        runtime_id="runtime-1",
        existing_entities_map={"endpoint-1": mock_assistant},
        seen_endpoint_ids=set(),
        aws_creds=mock_aws_creds,
    )

    assert result == []


# --- ARN bug fix tests ---


def _make_agentcore_assistant(runtime_arn=None, endpoint_arn=None):
    """Helper to build a mock assistant with given ARN values."""
    from codemie.rest_api.models.assistant import AssistantType

    assistant = MagicMock()
    assistant.type = AssistantType.BEDROCK_AGENTCORE_RUNTIME
    assistant.bedrock = None
    rt = MagicMock()
    rt.runtime_arn = runtime_arn
    rt.runtime_endpoint_arn = endpoint_arn
    rt.aws_settings_id = "setting-1"
    assistant.bedrock_agentcore_runtime = rt
    return assistant


def test_is_bedrock_assistant_uses_runtime_arn():
    """is_bedrock_assistant must check runtime_arn, not runtime_endpoint_arn."""
    from codemie.service.aws_bedrock.bedrock_orchestration_service import BedrockOrchestratorService

    assistant = _make_agentcore_assistant(
        runtime_arn="arn:aws:bedrock:us-east-1:123:runtime/r1",
        endpoint_arn=None,  # endpoint_arn absent — guard should still pass
    )
    assert BedrockOrchestratorService.is_bedrock_assistant(assistant) is True


def test_is_bedrock_assistant_false_when_no_runtime_arn():
    """is_bedrock_assistant must return False when runtime_arn is absent, even if endpoint_arn is present."""
    from codemie.service.aws_bedrock.bedrock_orchestration_service import BedrockOrchestratorService

    assistant = _make_agentcore_assistant(
        runtime_arn=None,
        endpoint_arn="some-endpoint-arn",  # endpoint_arn present — should still fail
    )
    assert BedrockOrchestratorService.is_bedrock_assistant(assistant) is False


# --- BedrockAgentcoreRuntimeData model tests ---


def test_bedrock_agentcore_runtime_data_reads_configuration_json():
    data = BedrockAgentcoreRuntimeData.model_validate(
        {
            "runtime_id": "r1",
            "runtime_arn": "arn:aws:bedrock:us-east-1:123:agentruntime/r1",
            "runtime_endpoint_id": "ep1",
            "runtime_endpoint_arn": "arn:aws:bedrock:us-east-1:123:agentruntime/r1/endpoint/ep1",
            "runtime_endpoint_name": "my-endpoint",
            "runtime_endpoint_live_version": "1",
            "aws_settings_id": "s1",
            "configuration_json": '{"response": {"streaming": false, "body": {"text_path": "output"}}}',
        }
    )
    assert data.configuration_json is not None
    assert "streaming" in data.configuration_json


# --- Wire parsers into invocation tests ---


def _make_assistant_with_config(configuration_json_str: str):
    assistant = MagicMock()
    rt = MagicMock()
    rt.runtime_arn = "arn:aws:bedrock:us-east-1:123:agentruntime/r1"
    rt.runtime_endpoint_name = "my-endpoint"
    rt.aws_settings_id = "setting-1"
    rt.configuration_json = configuration_json_str
    assistant.bedrock_agentcore_runtime = rt
    return assistant


def test_invoke_uses_new_config_request_path():
    """When configuration_json has message_path='prompt', request payload uses that key."""
    from codemie.service.aws_bedrock.bedrock_agentcore_runtime_service import BedrockAgentCoreRuntimeService

    config_json = json.dumps(
        {
            "request": {"message_path": "prompt"},
            "response": {"streaming": False, "body": {"text_path": "answer"}},
        }
    )
    assistant = _make_assistant_with_config(config_json)

    with (
        patch(
            "codemie.service.aws_bedrock.bedrock_agentcore_runtime_service.get_setting_aws_credentials"
        ) as mock_creds,
        patch.object(BedrockAgentCoreRuntimeService, "_bedrock_invoke_runtime") as mock_invoke,
    ):
        mock_creds.return_value = MagicMock(
            region="us-east-1", access_key_id="k", secret_access_key="s", session_token=None
        )
        mock_invoke.return_value = json.dumps({"answer": "hello"}).encode()

        result = BedrockAgentCoreRuntimeService.invoke_agentcore_runtime(
            assistant=assistant, input_text="my question", conversation_id="conv-1"
        )

    # Verify payload was built with message_path="prompt"
    call_kwargs = mock_invoke.call_args
    raw_payload = call_kwargs.kwargs.get("payload") or (call_kwargs.args[2] if len(call_kwargs.args) > 2 else None)
    payload = json.loads(raw_payload)
    assert payload == {"prompt": "my question"}
    assert result["output"] == "hello"
    assert "thoughts" in result


def test_invoke_json_mode_sends_application_json_accept():
    """Non-streaming config must send accept=application/json."""
    from codemie.service.aws_bedrock.bedrock_agentcore_runtime_service import BedrockAgentCoreRuntimeService

    config_json = json.dumps(
        {
            "request": {"message_path": "message"},
            "response": {"streaming": False, "body": {"text_path": "output"}},
        }
    )
    assistant = _make_assistant_with_config(config_json)

    with (
        patch(
            "codemie.service.aws_bedrock.bedrock_agentcore_runtime_service.get_setting_aws_credentials"
        ) as mock_creds,
        patch.object(BedrockAgentCoreRuntimeService, "_bedrock_invoke_runtime") as mock_invoke,
    ):
        mock_creds.return_value = MagicMock(
            region="us-east-1", access_key_id="k", secret_access_key="s", session_token=None
        )
        mock_invoke.return_value = json.dumps({"output": "hi"}).encode()
        BedrockAgentCoreRuntimeService.invoke_agentcore_runtime(
            assistant=assistant, input_text="q", conversation_id="c"
        )

    call_kwargs = mock_invoke.call_args
    accept = call_kwargs.kwargs.get("accept")
    assert accept == "application/json"


def test_invoke_streaming_sends_text_event_stream_accept():
    """Streaming config must send accept=text/event-stream."""
    from codemie.service.aws_bedrock.bedrock_agentcore_runtime_service import BedrockAgentCoreRuntimeService

    config_json = json.dumps(
        {
            "request": {"message_path": "message"},
            "response": {"streaming": True, "chunk": {"text_path": "delta"}},
        }
    )
    assistant = _make_assistant_with_config(config_json)
    mock_stream = MagicMock()
    mock_stream.iter_lines.return_value = [
        b'data: {"delta": "hello"}',
        b"",
    ]

    with (
        patch(
            "codemie.service.aws_bedrock.bedrock_agentcore_runtime_service.get_setting_aws_credentials"
        ) as mock_creds,
        patch.object(BedrockAgentCoreRuntimeService, "_bedrock_invoke_runtime") as mock_invoke,
    ):
        mock_creds.return_value = MagicMock(
            region="us-east-1", access_key_id="k", secret_access_key="s", session_token=None
        )
        mock_invoke.return_value = mock_stream
        BedrockAgentCoreRuntimeService.invoke_agentcore_runtime(
            assistant=assistant, input_text="q", conversation_id="c"
        )

    call_kwargs = mock_invoke.call_args
    accept = call_kwargs.kwargs.get("accept")
    assert accept == "text/event-stream"


# --- Thought emission in _agent_streaming tests ---


def test_agent_streaming_calls_process_output_with_response():
    """_agent_streaming delegates to agent_executor.stream() and calls process_output with the output chunk."""
    from codemie.agents.assistant_agent import AIToolsAgent

    agent = MagicMock()
    agent.thread_generator.is_closed.return_value = False
    agent._get_run_config.return_value = {}
    agent._get_inputs.return_value = {"input": "hello"}
    agent.agent_executor.stream.return_value = iter([{"output": "the answer"}])

    with patch.object(AIToolsAgent, "process_output") as mock_process_output:
        chunks = []
        AIToolsAgent._agent_streaming(agent, chunks)

    mock_process_output.assert_called_once_with("the answer", chunks)


def test_agent_streaming_no_thoughts_no_send():
    """_agent_streaming does not call thread_generator.send directly; streaming side-effects happen inside invoke_agentcore_runtime."""
    from codemie.agents.assistant_agent import AIToolsAgent

    agent = MagicMock()
    agent.thread_generator.is_closed.return_value = False
    agent._get_run_config.return_value = {}
    agent._get_inputs.return_value = {"input": "hello"}
    agent.agent_executor.stream.return_value = iter([{"output": "the answer"}])

    with patch.object(AIToolsAgent, "process_output"):
        chunks = []
        AIToolsAgent._agent_streaming(agent, chunks)

    agent.thread_generator.send.assert_not_called()


# --- Entity response model test ---


def test_endpoint_entity_exposes_configuration_json():
    """Entity builder must read configuration_json."""
    rt = MagicMock()
    rt.runtime_id = "rt-1"
    rt.runtime_arn = "arn:aws:bedrock:us-east-1:123:agentruntime/rt-1"
    rt.runtime_endpoint_id = "ep-1"
    rt.runtime_endpoint_name = "my-endpoint"
    rt.runtime_endpoint_live_version = "2"
    rt.runtime_endpoint_description = "desc"
    rt.configuration_json = '{"response": {"streaming": false, "body": {"text_path": "output"}}}'

    mock_assistant = MagicMock()
    mock_assistant.id = "assistant-uuid"
    mock_assistant.bedrock_agentcore_runtime = rt

    entity = BedrockAgentCoreEndpointService._build_endpoint_entity(None, mock_assistant)
    assert entity.configurationJson == rt.configuration_json


# --- Tests for history threading through invoke_agentcore_runtime ---


@patch(f"{_RUNTIME_MOD}.get_setting_aws_credentials")
@patch(f"{_RUNTIME_MOD}.BedrockAgentCoreRuntimeService._bedrock_invoke_runtime")
@patch(f"{_RUNTIME_MOD}.AgentcoreRequestBuilder")
def test_invoke_agentcore_runtime_passes_history_to_builder(
    mock_builder_cls,
    mock_bedrock_invoke_runtime,
    mock_get_setting_aws_credentials,
    mock_aws_creds,
):
    import json
    from codemie.core.models import ChatMessage
    from codemie.core.constants import ChatRole

    mock_get_setting_aws_credentials.return_value = mock_aws_creds
    mock_instance = MagicMock()
    mock_instance.build.return_value = b'{"query":"hi"}'
    mock_builder_cls.return_value = mock_instance
    mock_bedrock_invoke_runtime.return_value = b'{"output": "ok"}'

    configuration_json = json.dumps(
        {
            "request": {
                "message_path": "query",
                "history": {"history_path": "messages"},
            },
            "response": {"streaming": False, "body": {"text_path": "output"}},
        }
    )

    mock_assistant = MagicMock()
    mock_assistant.bedrock_agentcore_runtime.runtime_arn = "arn:test"
    mock_assistant.bedrock_agentcore_runtime.runtime_endpoint_name = "ep"
    mock_assistant.bedrock_agentcore_runtime.aws_settings_id = "setting-1"
    mock_assistant.bedrock_agentcore_runtime.configuration_json = configuration_json

    history = [ChatMessage(role=ChatRole.USER, message="prev")]

    BedrockAgentCoreRuntimeService.invoke_agentcore_runtime(
        assistant=mock_assistant,
        input_text="hi",
        conversation_id="conv-1",
        history=history,
    )

    call = mock_instance.build.call_args
    passed_history = call.kwargs.get("history") if call.kwargs and "history" in call.kwargs else call.args[1]
    assert passed_history == history


@patch(f"{_RUNTIME_MOD}.get_setting_aws_credentials")
@patch(f"{_RUNTIME_MOD}.BedrockAgentCoreRuntimeService._bedrock_invoke_runtime")
def test_invoke_agentcore_runtime_no_history_arg_backward_compat(
    mock_bedrock_invoke_runtime,
    mock_get_setting_aws_credentials,
    mock_aws_creds,
):
    import json as _json

    mock_get_setting_aws_credentials.return_value = mock_aws_creds
    mock_stream = MagicMock()
    mock_stream.iter_lines.return_value = [b'{"text": "response"}']
    mock_bedrock_invoke_runtime.return_value = mock_stream

    mock_assistant = MagicMock()
    mock_assistant.bedrock_agentcore_runtime.runtime_arn = "arn:test"
    mock_assistant.bedrock_agentcore_runtime.runtime_endpoint_name = "ep"
    mock_assistant.bedrock_agentcore_runtime.aws_settings_id = "setting-1"
    mock_assistant.bedrock_agentcore_runtime.configuration_json = _json.dumps(
        {"request": {"message_path": "message"}, "response": {"streaming": True, "chunk": {"text_path": "text"}}}
    )

    response = BedrockAgentCoreRuntimeService.invoke_agentcore_runtime(
        assistant=mock_assistant,
        input_text="hi",
        conversation_id="conv-1",
    )
    assert response["output"] == "response"


@patch(f"{_RUNTIME_MOD}.get_setting_aws_credentials")
@patch(f"{_RUNTIME_MOD}.BedrockAgentCoreRuntimeService._bedrock_invoke_runtime")
def test_invoke_agentcore_runtime_progressive_streaming(
    mock_bedrock_invoke_runtime,
    mock_get_setting_aws_credentials,
    mock_aws_creds,
):
    """When thread_generator is provided, each SSE text chunk is sent immediately via generated_chunk."""
    import json as _json

    mock_get_setting_aws_credentials.return_value = mock_aws_creds
    mock_stream = MagicMock()
    mock_stream.iter_lines.return_value = [
        b"data: " + _json.dumps({"text": "Hello"}).encode(),
        b"data: " + _json.dumps({"text": " world"}).encode(),
    ]
    mock_bedrock_invoke_runtime.return_value = mock_stream

    mock_assistant = MagicMock()
    mock_assistant.bedrock_agentcore_runtime.runtime_arn = "arn:test"
    mock_assistant.bedrock_agentcore_runtime.runtime_endpoint_name = "ep"
    mock_assistant.bedrock_agentcore_runtime.aws_settings_id = "setting-1"
    mock_assistant.bedrock_agentcore_runtime.configuration_json = _json.dumps(
        {"request": {"message_path": "message"}, "response": {"streaming": True, "chunk": {"text_path": "text"}}}
    )

    thread_gen = MagicMock()
    response = BedrockAgentCoreRuntimeService.invoke_agentcore_runtime(
        assistant=mock_assistant,
        input_text="hi",
        conversation_id="conv-1",
        thread_generator=thread_gen,
    )

    assert response["output"] == "Hello world"
    assert response["thoughts"] == []
    # Two text chunks + one last=True terminator
    assert thread_gen.send.call_count == 3
    sent_payloads = [_json.loads(call.args[0]) for call in thread_gen.send.call_args_list]
    chunk_payloads = sent_payloads[:2]
    assert all(p.get("generated_chunk") is not None for p in chunk_payloads)
    assert [p["generated_chunk"] for p in chunk_payloads] == ["Hello", " world"]
    last_payload = sent_payloads[2]
    assert last_payload.get("last") is True
    assert last_payload.get("generated_chunk") == ""


class TestParseAgentcoreJsonResponse:
    """Unit tests for _parse_agentcore_json_response."""

    def _make_config(self):
        from codemie.service.aws_bedrock.agentcore.agentcore_config import AgentcoreResponseConfig

        return AgentcoreResponseConfig.model_validate(
            {
                "streaming": False,
                "body": {"text_path": "output"},
            }
        )

    def _make_config_with_reasoning(self):
        from codemie.service.aws_bedrock.agentcore.agentcore_config import AgentcoreResponseConfig

        return AgentcoreResponseConfig.model_validate(
            {
                "streaming": False,
                "body": {
                    "text_path": "output",
                    "reasoning": {"text_path": "thought"},
                },
            }
        )

    def test_no_thread_generator_returns_text_and_thoughts(self):
        """Without thread_generator thoughts are returned directly."""
        import json as _json

        config = self._make_config_with_reasoning()
        raw = MagicMock()
        raw.decode.return_value = _json.dumps({"output": "hello", "thought": "thinking..."})

        text, thoughts = BedrockAgentCoreRuntimeService._parse_agentcore_json_response(raw, config)

        assert text == "hello"
        assert len(thoughts) == 1
        assert thoughts[0].message == "thinking..."

    def test_no_thread_generator_no_thoughts(self):
        """Without reasoning config the thoughts list is empty."""
        import json as _json

        config = self._make_config()
        raw = MagicMock()
        raw.decode.return_value = _json.dumps({"output": "hello"})

        text, thoughts = BedrockAgentCoreRuntimeService._parse_agentcore_json_response(raw, config)

        assert text == "hello"
        assert thoughts == []

    def test_with_thread_generator_sends_thoughts_and_returns_empty_list(self):
        """With thread_generator each thought is forwarded via send() and [] is returned."""
        import json as _json

        config = self._make_config_with_reasoning()
        raw = MagicMock()
        raw.decode.return_value = _json.dumps({"output": "hello", "thought": "thinking..."})
        thread_gen = MagicMock()

        text, thoughts = BedrockAgentCoreRuntimeService._parse_agentcore_json_response(raw, config, thread_gen)

        assert text == "hello"
        assert thoughts == []
        assert thread_gen.send.call_count == 1
        payload = _json.loads(thread_gen.send.call_args[0][0])
        assert payload["thought"]["message"] == "thinking..."

    def test_with_thread_generator_no_thoughts_sends_nothing(self):
        """With thread_generator but no thoughts, send() is never called."""
        import json as _json

        config = self._make_config()
        raw = MagicMock()
        raw.decode.return_value = _json.dumps({"output": "hello"})
        thread_gen = MagicMock()

        text, thoughts = BedrockAgentCoreRuntimeService._parse_agentcore_json_response(raw, config, thread_gen)

        assert text == "hello"
        assert thoughts == []
        thread_gen.send.assert_not_called()


class TestParseAgentcoreStreamingResponse:
    """Unit tests for _parse_agentcore_streaming_response."""

    def _make_config(self):
        from codemie.service.aws_bedrock.agentcore.agentcore_config import AgentcoreResponseConfig

        return AgentcoreResponseConfig.model_validate(
            {
                "streaming": True,
                "chunk": {"text_path": "token"},
            }
        )

    @patch(f"{_RUNTIME_MOD}._agentcore_response_parser")
    def test_no_thread_generator_delegates_to_parse_streaming(self, mock_parser):
        """Without thread_generator parse_streaming is called and its result returned."""
        config = self._make_config()
        mock_parser.parse_streaming.return_value = ("full text", [])

        result = BedrockAgentCoreRuntimeService._parse_agentcore_streaming_response(MagicMock(), config)

        assert result == ("full text", [])
        mock_parser.parse_streaming.assert_called_once()

    @patch(f"{_RUNTIME_MOD}._agentcore_response_parser")
    def test_with_thread_generator_sends_chunks_and_thoughts(self, mock_parser):
        """With thread_generator each chunk and thought is forwarded via send()."""
        import json as _json
        from codemie.chains.base import Thought, ThoughtAuthorType

        config = self._make_config()
        thought = Thought(id="t1", message="step", author_type=ThoughtAuthorType.Agent, in_progress=False)
        mock_parser.parse_streaming.return_value = iter(
            [
                (None, [thought]),
                ("Hello", []),
                (" world", []),
            ]
        )
        thread_gen = MagicMock()

        text, thoughts = BedrockAgentCoreRuntimeService._parse_agentcore_streaming_response(
            MagicMock(), config, thread_gen
        )

        assert text == "Hello world"
        assert thoughts == []
        sent_payloads = [_json.loads(c.args[0]) for c in thread_gen.send.call_args_list]
        assert sent_payloads[0]["thought"]["message"] == "step"
        assert sent_payloads[1]["generated_chunk"] == "Hello"
        assert sent_payloads[2]["generated_chunk"] == " world"


class TestParseAgentcoreResponse:
    """Unit tests for _parse_agentcore_response routing."""

    def _make_json_config(self):
        from codemie.service.aws_bedrock.agentcore.agentcore_config import AgentcoreResponseConfig

        return AgentcoreResponseConfig.model_validate(
            {
                "streaming": False,
                "body": {"text_path": "output"},
            }
        )

    def _make_streaming_config(self):
        from codemie.service.aws_bedrock.agentcore.agentcore_config import AgentcoreResponseConfig

        return AgentcoreResponseConfig.model_validate(
            {
                "streaming": True,
                "chunk": {"text_path": "token"},
            }
        )

    @patch.object(BedrockAgentCoreRuntimeService, "_parse_agentcore_json_response", return_value=("json out", []))
    def test_routes_to_json_when_not_streaming(self, mock_json):
        """Non-streaming config delegates to _parse_agentcore_json_response."""
        raw = MagicMock()
        config = self._make_json_config()

        result = BedrockAgentCoreRuntimeService._parse_agentcore_response(raw, config)

        mock_json.assert_called_once_with(raw, config, None)
        assert result == ("json out", [])

    @patch.object(BedrockAgentCoreRuntimeService, "_parse_agentcore_streaming_response", return_value=("sse out", []))
    def test_routes_to_streaming_when_streaming(self, mock_sse):
        """Streaming config delegates to _parse_agentcore_streaming_response."""
        raw = MagicMock()
        config = self._make_streaming_config()

        result = BedrockAgentCoreRuntimeService._parse_agentcore_response(raw, config)

        mock_sse.assert_called_once_with(raw, config, None)
        assert result == ("sse out", [])

    @patch.object(BedrockAgentCoreRuntimeService, "_parse_agentcore_json_response", return_value=("out", []))
    def test_passes_thread_generator_through(self, mock_json):
        """thread_generator is forwarded to the delegated method."""
        raw = MagicMock()
        config = self._make_json_config()
        tg = MagicMock()

        BedrockAgentCoreRuntimeService._parse_agentcore_response(raw, config, tg)

        mock_json.assert_called_once_with(raw, config, tg)
