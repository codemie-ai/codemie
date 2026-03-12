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
from codemie.rest_api.models.vendor import ImportFlow
from codemie.core.exceptions import ExtendedHTTPException
from codemie.service.aws_bedrock.bedrock_flow_service import BedrockFlowService
from codemie.rest_api.models.settings import AWSCredentials, Settings
from codemie.rest_api.security.user import User


@pytest.fixture
def mock_user():
    user = MagicMock(spec=User)
    user.id = "user-id"
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
def flow_data():
    return [
        {
            "id": "flow-1",
            "name": "Flow 1",
            "status": "Prepared",
            "version": "1",
            "updatedAt": "2024-01-01T00:00:00Z",
        },
        {
            "id": "flow-2",
            "name": "Flow 2",
            "status": "NotPrepared",
            "version": "2",
            "updatedAt": "2024-01-02T00:00:00Z",
        },
    ]


@pytest.fixture
def flow_aliases():
    return [
        {
            "id": "alias-1",
            "name": "Alias 1",
            "routingConfiguration": [{"flowVersion": "1"}],
            "flowId": "flow-1",
            "description": "Test alias 1",
            "arn": "arn:aws:bedrock:us-east-1:123456789012:flow-alias/alias-1",
            "createdAt": "2024-01-01T00:00:00Z",
            "updatedAt": "2024-01-01T00:00:00Z",
        },
        {
            "id": "alias-2",
            "name": "Alias 2",
            "routingConfiguration": [{"flowVersion": "2"}],
            "flowId": "flow-2",
            "description": "Test alias 2",
            "arn": "arn:aws:bedrock:us-east-1:123456789012:flow-alias/alias-2",
            "createdAt": "2024-01-02T00:00:00Z",
            "updatedAt": "2024-01-02T00:00:00Z",
        },
        {
            "id": "alias-draft",
            "name": "Alias Draft",
            "routingConfiguration": [{"flowVersion": "DRAFT"}],
            "flowId": "flow-1",
            "description": "Draft alias",
            "arn": "arn:aws:bedrock:us-east-1:123456789012:flow-alias/alias-draft",
            "createdAt": "2024-01-01T00:00:00Z",
            "updatedAt": "2024-01-01T00:00:00Z",
        },
        {
            "id": "alias-norc",
            "name": "Alias NoRC",
            "flowId": "flow-1",
            "description": "No routing config alias",
            "arn": "arn:aws:bedrock:us-east-1:123456789012:flow-alias/alias-norc",
            "createdAt": "2024-01-01T00:00:00Z",
            "updatedAt": "2024-01-01T00:00:00Z",
            # No routingConfiguration
        },
    ]


@pytest.fixture
def flow_version_info():
    return {
        "id": "flow-1",
        "name": "Flow 1",
        "arn": "arn:aws:bedrock:us-east-1:123456789012:flow/flow-1",
        "version": "1",
        "status": "Prepared",
        "description": "Test flow description",
        "executionRoleArn": "arn:aws:iam::123456789012:role/FlowRole",
        "customerEncryptionKeyArn": "arn:aws:kms:us-east-1:123456789012:key/12345678-1234-1234-1234-123456789012",
        "createdAt": "2024-01-01T00:00:00Z",
    }


@pytest.fixture
def flow_import():
    return ImportFlow(id="flow-1", flowAliasId="alias-1", setting_id="setting-1")


# --- Tests for get_all_settings_overview ---
@patch("codemie.service.aws_bedrock.bedrock_flow_service.get_all_settings_for_user")
@patch("codemie.service.aws_bedrock.bedrock_flow_service.BedrockFlowService._fetch_main_entity_names_for_setting")
def test_get_all_settings_overview_success(
    mock_fetch_main_entity_names_for_setting,
    mock_get_all_settings_for_user,
    mock_user,
    flow_data,
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

    # Mock the fetch method to return flow names for different settings
    mock_fetch_main_entity_names_for_setting.side_effect = [
        ["Flow 1"],  # For setting-1
        ["Flow 2"],  # For setting-2
    ]

    result = BedrockFlowService.get_all_settings_overview(mock_user, page=0, per_page=10)

    # Assertions
    assert "data" in result
    assert "pagination" in result
    assert len(result["data"]) == 2

    # Check first setting
    setting1_data = result["data"][0]
    assert setting1_data["setting_id"] == "setting-1"
    assert setting1_data["setting_name"] == "Setting 1"
    assert setting1_data["project"] == "project-1"
    assert setting1_data["entities"] == ["Flow 1"]

    # Check second setting
    setting2_data = result["data"][1]
    assert setting2_data["setting_id"] == "setting-2"
    assert setting2_data["setting_name"] == "Setting 2"
    assert setting2_data["project"] == "project-2"
    assert setting2_data["entities"] == ["Flow 2"]


@patch("codemie.service.aws_bedrock.bedrock_flow_service.get_all_settings_for_user")
@patch("codemie.service.aws_bedrock.bedrock_flow_service.BedrockFlowService._fetch_main_entity_names_for_setting")
def test_get_all_settings_overview_empty_settings(
    mock_fetch_main_entity_names_for_setting,
    mock_get_all_settings_for_user,
    mock_user,
):
    """Test get_all_settings_overview with no settings available."""
    mock_get_all_settings_for_user.return_value = []

    result = BedrockFlowService.get_all_settings_overview(mock_user, page=0, per_page=10)

    assert result["data"] == []
    assert result["pagination"]["total"] == 0
    assert result["pagination"]["pages"] == 0
    assert result["pagination"]["page"] == 0
    assert result["pagination"]["per_page"] == 10

    # Should not call the fetch method when there are no settings
    mock_fetch_main_entity_names_for_setting.assert_not_called()


@patch("codemie.service.aws_bedrock.bedrock_flow_service.get_all_settings_for_user")
@patch("codemie.service.aws_bedrock.bedrock_flow_service.BedrockFlowService._fetch_main_entity_names_for_setting")
def test_get_all_settings_overview_pagination(
    mock_fetch_main_entity_names_for_setting,
    mock_get_all_settings_for_user,
    mock_user,
):
    """Test get_all_settings_overview pagination works correctly."""
    # Create 5 settings
    settings = []
    for i in range(5):
        setting = MagicMock()
        setting.id = f"setting-{i}"
        setting.alias = f"Setting {i}"
        setting.project_name = f"project-{i}"
        settings.append(setting)

    mock_get_all_settings_for_user.return_value = settings
    mock_fetch_main_entity_names_for_setting.return_value = ["Flow Test"]

    # Test first page (page=0, per_page=2)
    result = BedrockFlowService.get_all_settings_overview(mock_user, page=0, per_page=2)

    assert len(result["data"]) == 2
    assert result["pagination"]["total"] == 5
    assert result["pagination"]["pages"] == 3  # ceil(5/2)
    assert result["pagination"]["page"] == 0
    assert result["pagination"]["per_page"] == 2
    assert result["data"][0]["setting_id"] == "setting-0"
    assert result["data"][1]["setting_id"] == "setting-1"


@patch("codemie.service.aws_bedrock.bedrock_flow_service.get_all_settings_for_user")
@patch("codemie.service.aws_bedrock.bedrock_flow_service.BedrockFlowService._fetch_main_entity_names_for_setting")
def test_get_all_settings_overview_handles_aws_exceptions(
    mock_fetch_main_entity_names_for_setting,
    mock_get_all_settings_for_user,
    mock_user,
    mock_setting,
):
    """Test get_all_settings_overview handles AWS exceptions gracefully."""
    mock_get_all_settings_for_user.return_value = [mock_setting]
    mock_fetch_main_entity_names_for_setting.side_effect = Exception("AWS error")

    # Should not raise an exception, but continue processing
    result = BedrockFlowService.get_all_settings_overview(mock_user, page=0, per_page=10)
    assert len(result["data"]) == 1
    assert result["data"][0]["setting_id"] == "setting-1"
    assert result["data"][0]["error"] == "AWS error"
    assert result["data"][0]["invalid"] is False


@patch("codemie.service.aws_bedrock.bedrock_flow_service.get_all_settings_for_user")
@patch("codemie.service.aws_bedrock.bedrock_flow_service.get_setting_aws_credentials")
@patch("codemie.service.aws_bedrock.bedrock_flow_service.BedrockFlowService._bedrock_list_flows")
def test_get_all_settings_overview_limits_entity_count(
    mock_bedrock_list_flows,
    mock_get_setting_aws_credentials,
    mock_get_all_settings_for_user,
    mock_user,
    mock_setting,
    mock_aws_creds,
):
    """Test get_all_settings_overview limits entity count to ALL_SETTINGS_OVERVIEW_ENTITY_COUNT."""
    mock_get_all_settings_for_user.return_value = [mock_setting]
    mock_get_setting_aws_credentials.return_value = mock_aws_creds

    # Create more than 4 flows
    many_flows = []
    for i in range(10):
        many_flows.append({"name": f"Flow {i}"})

    mock_bedrock_list_flows.return_value = many_flows, None

    result = BedrockFlowService.get_all_settings_overview(mock_user, page=0, per_page=10)

    # Should limit to 4 flows
    setting_data = result["data"][0]
    assert len(setting_data["entities"]) == 4


# --- Tests for list_main_entities ---
@patch("codemie.service.aws_bedrock.bedrock_flow_service.get_setting_for_user")
@patch("codemie.service.aws_bedrock.bedrock_flow_service.get_setting_aws_credentials")
@patch("codemie.service.aws_bedrock.bedrock_flow_service.BedrockFlowService._bedrock_list_flows")
def test_list_main_entities_success(
    mock_bedrock_list_flows,
    mock_get_setting_aws_credentials,
    mock_get_setting_for_user,
    mock_user,
    mock_setting,
    mock_aws_creds,
    flow_data,
):
    """Test list_main_entities returns correct flow data."""
    mock_get_setting_for_user.return_value = mock_setting
    mock_get_setting_aws_credentials.return_value = mock_aws_creds
    mock_bedrock_list_flows.return_value = (flow_data, None)

    result, next_token = BedrockFlowService.list_main_entities(mock_user, "setting-1", page=0, per_page=10)

    assert len(result) == 2
    assert result[0]["id"] == "flow-1"
    assert result[0]["name"] == "Flow 1"
    assert result[0]["status"] == "PREPARED"
    assert result[1]["id"] == "flow-2"
    assert result[1]["name"] == "Flow 2"
    assert result[1]["status"] == "NOT_PREPARED"
    assert next_token is None


@patch("codemie.service.aws_bedrock.bedrock_flow_service.get_setting_for_user")
@patch("codemie.service.aws_bedrock.bedrock_flow_service.get_setting_aws_credentials")
@patch("codemie.service.aws_bedrock.bedrock_flow_service.BedrockFlowService._bedrock_list_flows")
def test_list_main_entities_empty(
    mock_bedrock_list_flows,
    mock_get_setting_aws_credentials,
    mock_get_setting_for_user,
    mock_user,
    mock_setting,
    mock_aws_creds,
):
    """Test list_main_entities with no flows."""
    mock_get_setting_for_user.return_value = mock_setting
    mock_get_setting_aws_credentials.return_value = mock_aws_creds
    mock_bedrock_list_flows.return_value = ([], None)

    result, next_token = BedrockFlowService.list_main_entities(mock_user, "setting-1", page=0, per_page=10)

    assert result == []
    assert next_token is None


# --- Tests for get_main_entity_detail ---
@patch("codemie.service.aws_bedrock.bedrock_flow_service.get_setting_for_user")
@patch("codemie.service.aws_bedrock.bedrock_flow_service.get_setting_aws_credentials")
@patch("codemie.service.aws_bedrock.bedrock_flow_service.BedrockFlowService._bedrock_get_flow")
def test_get_main_entity_detail_success(
    mock_bedrock_get_flow,
    mock_get_setting_aws_credentials,
    mock_get_setting_for_user,
    mock_user,
    mock_setting,
    mock_aws_creds,
):
    """Test get_main_entity_detail returns correct flow information."""
    mock_get_setting_for_user.return_value = mock_setting
    mock_get_setting_aws_credentials.return_value = mock_aws_creds

    flow_detail = {
        "id": "flow-1",
        "name": "Flow 1",
        "description": "Test flow description",
        "status": "Prepared",
        "version": "1",
        "createdAt": "2024-01-01T00:00:00Z",
        "updatedAt": "2024-01-01T00:00:00Z",
    }
    mock_bedrock_get_flow.return_value = flow_detail

    result = BedrockFlowService.get_main_entity_detail(mock_user, "flow-1", "setting-1")

    assert result["id"] == "flow-1"
    assert result["name"] == "Flow 1"
    assert result["description"] == "Test flow description"
    assert result["status"] == "PREPARED"
    assert result["version"] == "1"

    mock_get_setting_for_user.assert_called_once_with(mock_user, "setting-1")
    mock_get_setting_aws_credentials.assert_called_once_with(mock_setting.id)
    mock_bedrock_get_flow.assert_called_once_with(
        flow_id="flow-1",
        region=mock_aws_creds.region,
        access_key_id=mock_aws_creds.access_key_id,
        secret_access_key=mock_aws_creds.secret_access_key,
        session_token=mock_aws_creds.session_token,
    )


@patch("codemie.service.aws_bedrock.bedrock_flow_service.get_setting_for_user")
@patch("codemie.service.aws_bedrock.bedrock_flow_service.get_setting_aws_credentials")
@patch("codemie.service.aws_bedrock.bedrock_flow_service.BedrockFlowService._bedrock_get_flow")
def test_get_main_entity_detail_not_found(
    mock_bedrock_get_flow,
    mock_get_setting_aws_credentials,
    mock_get_setting_for_user,
    mock_user,
    mock_setting,
    mock_aws_creds,
):
    """Test get_main_entity_detail when flow is not found."""
    mock_get_setting_for_user.return_value = mock_setting
    mock_get_setting_aws_credentials.return_value = mock_aws_creds
    mock_bedrock_get_flow.return_value = None

    with pytest.raises(ExtendedHTTPException):
        BedrockFlowService.get_main_entity_detail(mock_user, "flow-1", "setting-1")


# --- Tests for list_importable_entities_for_main_entity ---
@patch("codemie.service.aws_bedrock.bedrock_flow_service.get_setting_for_user")
@patch("codemie.service.aws_bedrock.bedrock_flow_service.get_setting_aws_credentials")
@patch("codemie.service.aws_bedrock.bedrock_flow_service.WorkflowConfig.get_by_bedrock_aws_settings_id")
@patch("codemie.service.aws_bedrock.bedrock_flow_service.BedrockFlowService._bedrock_list_flow_aliases")
def test_list_importable_entities_for_main_entity_success(
    mock_list_flow_aliases,
    mock_get_by_bedrock_aws_settings_id,
    mock_get_setting_aws_credentials,
    mock_get_setting_for_user,
    mock_user,
    mock_setting,
    mock_aws_creds,
    flow_aliases,
):
    """Test list_importable_entities_for_main_entity returns correct alias data."""
    mock_get_setting_for_user.return_value = mock_setting
    mock_get_setting_aws_credentials.return_value = mock_aws_creds
    mock_get_by_bedrock_aws_settings_id.return_value = []
    mock_list_flow_aliases.return_value = (flow_aliases, None)

    result, next_token = BedrockFlowService.list_importable_entities_for_main_entity(
        mock_user, "flow-1", "setting-1", page=0, per_page=10
    )

    assert len(result) == 4
    assert next_token is None

    # Check first alias (should be PREPARED)
    assert result[0]["id"] == "alias-1"
    assert result[0]["name"] == "Alias 1"
    assert result[0]["status"] == "PREPARED"
    assert result[0]["version"] == "1"

    # Check second alias (should be PREPARED)
    assert result[1]["id"] == "alias-2"
    assert result[1]["status"] == "PREPARED"

    # Check draft alias (should be NOT_PREPARED due to DRAFT version)
    assert result[2]["id"] == "alias-draft"
    assert result[2]["status"] == "NOT_PREPARED"

    # Check no routing config alias (should have None version)
    assert result[3]["id"] == "alias-norc"
    assert result[3]["version"] is None


@patch("codemie.service.aws_bedrock.bedrock_flow_service.get_setting_for_user")
@patch("codemie.service.aws_bedrock.bedrock_flow_service.get_setting_aws_credentials")
@patch("codemie.service.aws_bedrock.bedrock_flow_service.WorkflowConfig.get_by_bedrock_aws_settings_id")
@patch("codemie.service.aws_bedrock.bedrock_flow_service.BedrockFlowService._bedrock_list_flow_aliases")
def test_list_importable_entities_for_main_entity_with_existing_entities(
    mock_list_flow_aliases,
    mock_get_by_bedrock_aws_settings_id,
    mock_get_setting_aws_credentials,
    mock_get_setting_for_user,
    mock_user,
    mock_setting,
    mock_aws_creds,
    flow_aliases,
):
    """Test list_importable_entities_for_main_entity includes aiRunId for existing entities."""
    mock_get_setting_for_user.return_value = mock_setting
    mock_get_setting_aws_credentials.return_value = mock_aws_creds

    # Mock existing entity
    existing_workflow = MagicMock()
    existing_workflow.id = "workflow-123"
    existing_workflow.bedrock.bedrock_flow_alias_id = "alias-1"
    mock_get_by_bedrock_aws_settings_id.return_value = [existing_workflow]

    mock_list_flow_aliases.return_value = ([flow_aliases[0]], None)  # Only first alias

    result, next_token = BedrockFlowService.list_importable_entities_for_main_entity(
        mock_user, "flow-1", "setting-1", page=0, per_page=10
    )

    assert len(result) == 1
    assert result[0]["id"] == "alias-1"
    assert result[0]["aiRunId"] == "workflow-123"
    assert next_token is None


@patch("codemie.service.aws_bedrock.bedrock_flow_service.get_setting_for_user")
@patch("codemie.service.aws_bedrock.bedrock_flow_service.get_setting_aws_credentials")
@patch("codemie.service.aws_bedrock.bedrock_flow_service.WorkflowConfig.get_by_bedrock_aws_settings_id")
@patch("codemie.service.aws_bedrock.bedrock_flow_service.BedrockFlowService._bedrock_list_flow_aliases")
def test_list_importable_entities_for_main_entity_handles_exceptions(
    mock_list_flow_aliases,
    mock_get_by_bedrock_aws_settings_id,
    mock_get_setting_aws_credentials,
    mock_get_setting_for_user,
    mock_user,
    mock_setting,
    mock_aws_creds,
):
    """Test list_importable_entities_for_main_entity handles AWS exceptions."""
    mock_get_setting_for_user.return_value = mock_setting
    mock_get_setting_aws_credentials.return_value = mock_aws_creds
    mock_get_by_bedrock_aws_settings_id.return_value = []
    mock_list_flow_aliases.side_effect = Exception("AWS error")

    with pytest.raises(ExtendedHTTPException):
        BedrockFlowService.list_importable_entities_for_main_entity(
            mock_user, "flow-1", "setting-1", page=0, per_page=10
        )


# --- Tests for get_importable_entity_detail ---
@patch("codemie.service.aws_bedrock.bedrock_flow_service.get_setting_for_user")
@patch("codemie.service.aws_bedrock.bedrock_flow_service.get_setting_aws_credentials")
@patch("codemie.service.aws_bedrock.bedrock_flow_service.BedrockFlowService._bedrock_get_flow_version")
def test_get_importable_entity_detail_success(
    mock_get_flow_version,
    mock_get_setting_aws_credentials,
    mock_get_setting_for_user,
    mock_user,
    mock_setting,
    mock_aws_creds,
    flow_version_info,
):
    """Test get_importable_entity_detail returns correct version information."""
    mock_get_setting_for_user.return_value = mock_setting
    mock_get_setting_aws_credentials.return_value = mock_aws_creds
    mock_get_flow_version.return_value = flow_version_info

    result = BedrockFlowService.get_importable_entity_detail(mock_user, "flow-1", "1", "setting-1")

    assert result["id"] == "flow-1"
    assert result["name"] == "Flow 1"
    assert result["version"] == "1"
    assert result["description"] == "Test flow description"
    assert result["status"] == "PREPARED"


@patch("codemie.service.aws_bedrock.bedrock_flow_service.get_setting_for_user")
@patch("codemie.service.aws_bedrock.bedrock_flow_service.get_setting_aws_credentials")
@patch("codemie.service.aws_bedrock.bedrock_flow_service.BedrockFlowService._bedrock_get_flow_version")
def test_get_importable_entity_detail_not_found(
    mock_get_flow_version,
    mock_get_setting_aws_credentials,
    mock_get_setting_for_user,
    mock_user,
    mock_setting,
    mock_aws_creds,
):
    """Test get_importable_entity_detail when version info is not found."""
    mock_get_setting_for_user.return_value = mock_setting
    mock_get_setting_aws_credentials.return_value = mock_aws_creds
    mock_get_flow_version.return_value = None

    result = BedrockFlowService.get_importable_entity_detail(mock_user, "flow-1", "1", "setting-1")

    assert result == {}


# --- Tests for import_entities ---
@patch("codemie.service.aws_bedrock.bedrock_flow_service.get_setting_for_user")
@patch("codemie.service.aws_bedrock.bedrock_flow_service.get_setting_aws_credentials")
@patch("codemie.service.aws_bedrock.bedrock_flow_service.WorkflowConfig.get_by_bedrock_aws_settings_id")
@patch("codemie.service.aws_bedrock.bedrock_flow_service.BedrockFlowService._bedrock_get_flow_alias")
@patch("codemie.service.aws_bedrock.bedrock_flow_service.BedrockFlowService._validate_and_retrieve_alias_version")
@patch("codemie.service.aws_bedrock.bedrock_flow_service.BedrockFlowService._create_workflow_object")
@patch("codemie.service.aws_bedrock.bedrock_flow_service.BedrockFlowService._create_or_update_entity")
def test_import_entities_success(
    mock_create_or_update_entity,
    mock_create_workflow_object,
    mock_validate_and_retrieve_alias_version,
    mock_get_flow_alias,
    mock_get_by_bedrock_aws_settings_id,
    mock_get_aws_creds,
    mock_get_setting_for_user,
    mock_user,
    mock_setting,
    mock_aws_creds,
    flow_import,
):
    """Test import_entities successfully imports a flow."""
    mock_get_setting_for_user.return_value = mock_setting
    mock_get_aws_creds.return_value = mock_aws_creds
    mock_get_by_bedrock_aws_settings_id.return_value = []
    mock_get_flow_alias.return_value = {
        "id": "alias-1",
        "name": "Alias 1",
        "routingConfiguration": [{"flowVersion": "1"}],
        "outputs": [{"name": "document"}],
    }
    mock_validate_and_retrieve_alias_version.return_value = {
        "definition": {
            "nodes": [{"type": "Input", "name": "FlowInput", "outputs": [{"name": "document", "type": "String"}]}]
        },
        "status": "Prepared",
    }
    mock_create_workflow_object.return_value = MagicMock()
    mock_create_or_update_entity.return_value = "workflow-123"

    import_payload = {"setting-1": [flow_import]}
    result: list[dict] = BedrockFlowService.import_entities(mock_user, import_payload)  # type: ignore

    assert len(result) == 1
    assert result[0]["flowId"] == "flow-1"
    assert result[0]["flowAliasId"] == "alias-1"
    assert result[0]["aiRunId"] == "workflow-123"
    mock_create_or_update_entity.assert_called_once()


@patch("codemie.service.aws_bedrock.bedrock_flow_service.get_setting_for_user")
@patch("codemie.service.aws_bedrock.bedrock_flow_service.get_setting_aws_credentials")
@patch("codemie.service.aws_bedrock.bedrock_flow_service.WorkflowConfig.get_by_bedrock_aws_settings_id")
@patch("codemie.service.aws_bedrock.bedrock_flow_service.BedrockFlowService._bedrock_get_flow_alias")
@patch("codemie.service.aws_bedrock.bedrock_flow_service.BedrockFlowService._validate_and_retrieve_alias_version")
def test_import_entities_invalid_version(
    mock_validate_and_retrieve_alias_version,
    mock_get_flow_alias,
    mock_get_by_bedrock_aws_settings_id,
    mock_get_aws_creds,
    mock_get_setting_for_user,
    mock_user,
    mock_setting,
    mock_aws_creds,
    flow_import,
):
    """Test import_entities handles invalid version gracefully."""
    mock_get_setting_for_user.return_value = mock_setting
    mock_get_aws_creds.return_value = mock_aws_creds
    mock_get_by_bedrock_aws_settings_id.return_value = []
    mock_get_flow_alias.return_value = {
        "id": "alias-1",
        "name": "Alias 1",
        "routingConfiguration": [{"flowVersion": "1"}],
    }
    mock_validate_and_retrieve_alias_version.return_value = None

    import_payload = {"setting-1": [flow_import]}
    result: list[dict] = BedrockFlowService.import_entities(mock_user, import_payload)  # type: ignore

    assert len(result) == 1
    assert result[0]["flowId"] == "flow-1"
    assert result[0]["flowAliasId"] == "alias-1"
    assert "error" in result[0]
    assert result[0]["error"]["statusCode"] == "422"


# --- Tests for delete_entities ---
@patch("codemie.service.aws_bedrock.bedrock_flow_service.WorkflowConfig.get_by_bedrock_aws_settings_id")
@patch("codemie.service.aws_bedrock.bedrock_flow_service.WorkflowExecution.get_by_workflow_id")
@patch("codemie.service.aws_bedrock.bedrock_flow_service.WorkflowExecution.delete")
@patch("codemie.service.aws_bedrock.bedrock_flow_service.WorkflowConfig.delete")
@patch("codemie.service.guardrail.guardrail_service.GuardrailService.remove_guardrail_assignments_for_entity")
def test_delete_entities_deletes_all_workflows(
    mock_remove_guardrails,
    mock_workflowconfig_delete,
    mock_workflowexecution_delete,
    mock_workflowexecution_get_by_workflow_id,
    mock_get_by_bedrock_aws_settings_id,
):
    """Test delete_entities deletes all workflows and their executions."""
    mock_remove_guardrails.return_value = None

    mock_entity1 = MagicMock(id="wf-1")
    mock_entity2 = MagicMock(id="wf-2")
    mock_get_by_bedrock_aws_settings_id.return_value = [mock_entity1, mock_entity2]
    mock_workflowexecution_get_by_workflow_id.side_effect = [[MagicMock(id="ex-1")], [MagicMock(id="ex-2")]]

    BedrockFlowService.delete_entities("setting-1")

    mock_workflowexecution_delete.assert_any_call("ex-1")
    mock_workflowexecution_delete.assert_any_call("ex-2")
    mock_workflowconfig_delete.assert_any_call("wf-1")
    mock_workflowconfig_delete.assert_any_call("wf-2")


# --- Tests for invoke_flow ---
@patch("codemie.service.aws_bedrock.bedrock_flow_service.get_setting_for_user")
@patch("codemie.service.aws_bedrock.bedrock_flow_service.get_setting_aws_credentials")
@patch("codemie.service.aws_bedrock.bedrock_flow_service.BedrockFlowService._bedrock_invoke_flow")
def test_invoke_flow_success(
    mock_bedrock_invoke_flow,
    mock_get_setting_aws_credentials,
    mock_get_setting_for_user,
    mock_aws_creds,
    mock_user,
):
    """Test invoke_flow successfully invokes a flow."""
    mock_setting = MagicMock()
    mock_setting.id = "setting-1"
    mock_setting.user_id = "user-id"
    mock_get_setting_for_user.return_value = mock_setting
    mock_get_setting_aws_credentials.return_value = mock_aws_creds
    mock_bedrock_invoke_flow.return_value = "result"

    result = BedrockFlowService.invoke_flow(
        flow_id="flow-1",
        flow_alias_id="alias-1",
        user=mock_user,
        setting_id="setting-1",
        inputs=[{"content": {"document": "test"}, "nodeName": "FlowInput", "nodeOutputName": "document"}],
    )

    assert result["output"] == "result"
    assert "time_elapsed" in result


@patch("codemie.service.aws_bedrock.bedrock_flow_service.get_setting_for_user")
@patch("codemie.service.aws_bedrock.bedrock_flow_service.get_setting_aws_credentials")
@patch("codemie.service.aws_bedrock.bedrock_flow_service.BedrockFlowService._bedrock_invoke_flow")
def test_invoke_flow_client_error(
    mock_bedrock_invoke_flow,
    mock_get_setting_aws_creds,
    mock_get_setting_for_user,
    mock_aws_creds,
    mock_user,
):
    """Test invoke_flow handles client errors gracefully."""
    from botocore.exceptions import ClientError

    mock_setting = MagicMock()
    mock_setting.id = "setting-1"
    mock_setting.user_id = "user-id"
    mock_get_setting_for_user.return_value = mock_setting
    mock_get_setting_aws_creds.return_value = mock_aws_creds
    mock_bedrock_invoke_flow.side_effect = ClientError(
        {"Error": {"Code": "400", "Message": "Bad Request"}}, "invoke_flow"
    )

    result = BedrockFlowService.invoke_flow(
        flow_id="flow-1",
        flow_alias_id="alias-1",
        user=mock_user,
        setting_id="setting-1",
        inputs=[{"content": {"document": "test"}, "nodeName": "FlowInput", "nodeOutputName": "document"}],
    )

    assert "output" in result
    assert "Bad Request" in result["output"]


# --- Tests for validate_remote_entity_exists_and_cleanup ---
@patch("codemie.rest_api.models.settings.Settings.get_by_id")
@patch("codemie.service.aws_bedrock.bedrock_flow_service.get_setting_aws_credentials")
@patch("codemie.service.aws_bedrock.bedrock_flow_service.BedrockFlowService._bedrock_get_flow_alias")
def test_validate_remote_entity_exists_and_cleanup_success(
    mock_bedrock_get_flow_alias,
    mock_get_setting_aws_credentials,
    mock_settings_get_by_id,
    mock_aws_creds,
):
    """Test validate_remote_entity_exists_and_cleanup when flow alias exists remotely."""
    # Create mock workflow config with Bedrock configuration
    mock_workflow = MagicMock()
    mock_workflow.bedrock.bedrock_flow_id = "flow-123"
    mock_workflow.bedrock.bedrock_flow_alias_id = "alias-456"
    mock_workflow.bedrock.bedrock_aws_settings_id = "setting-789"
    mock_workflow.name = "Test Workflow"

    mock_setting = MagicMock()
    mock_settings_get_by_id.return_value = mock_setting
    mock_get_setting_aws_credentials.return_value = mock_aws_creds
    mock_bedrock_get_flow_alias.return_value = {"aliasId": "alias-456"}

    result = BedrockFlowService.validate_remote_entity_exists_and_cleanup(mock_workflow)

    assert result is None
    mock_settings_get_by_id.assert_called_once_with(id_="setting-789")
    mock_get_setting_aws_credentials.assert_called_once_with(mock_setting.id)
    mock_bedrock_get_flow_alias.assert_called_once_with(
        flow_id="flow-123",
        alias_id="alias-456",
        region=mock_aws_creds.region,
        access_key_id=mock_aws_creds.access_key_id,
        secret_access_key=mock_aws_creds.secret_access_key,
        session_token=mock_aws_creds.session_token,
    )


@patch("codemie.rest_api.models.settings.Settings.get_by_id")
@patch("codemie.service.aws_bedrock.bedrock_flow_service.get_setting_aws_credentials")
@patch("codemie.service.aws_bedrock.bedrock_flow_service.BedrockFlowService._bedrock_get_flow_alias")
@patch("codemie.service.aws_bedrock.bedrock_flow_service.WorkflowExecution.get_by_workflow_id")
@patch("codemie.service.aws_bedrock.bedrock_flow_service.WorkflowExecution.delete")
@patch("codemie.service.aws_bedrock.bedrock_flow_service.WorkflowConfig.delete")
@patch("codemie.service.guardrail.guardrail_service.GuardrailService.remove_guardrail_assignments_for_entity")
def test_validate_remote_entity_exists_and_cleanup_resource_not_found(
    mock_remove_guardrails,
    mock_workflow_config_delete,
    mock_workflow_execution_delete,
    mock_get_by_workflow_id,
    mock_bedrock_get_flow_alias,
    mock_get_setting_aws_credentials,
    mock_settings_get_by_id,
    mock_aws_creds,
):
    """Test validate_remote_entity_exists_and_cleanup when flow alias is deleted remotely."""
    from botocore.exceptions import ClientError

    mock_remove_guardrails.return_value = None

    # Create mock workflow config with Bedrock configuration
    mock_workflow = MagicMock()
    mock_workflow.id = "workflow-123"
    mock_workflow.bedrock.bedrock_flow_id = "flow-123"
    mock_workflow.bedrock.bedrock_flow_alias_id = "alias-456"
    mock_workflow.bedrock.bedrock_aws_settings_id = "setting-789"
    mock_workflow.name = "Test Workflow"

    mock_setting = MagicMock()
    mock_settings_get_by_id.return_value = mock_setting
    mock_get_setting_aws_credentials.return_value = mock_aws_creds

    # Mock ResourceNotFoundException
    error_response = {"Error": {"Code": "ResourceNotFoundException", "Message": "Flow alias not found"}}
    mock_bedrock_get_flow_alias.side_effect = ClientError(error_response, "GetFlowAlias")

    # Mock workflow executions
    mock_execution1 = MagicMock()
    mock_execution1.id = "exec-1"
    mock_execution2 = MagicMock()
    mock_execution2.id = "exec-2"
    mock_get_by_workflow_id.return_value = [mock_execution1, mock_execution2]

    result = BedrockFlowService.validate_remote_entity_exists_and_cleanup(mock_workflow)

    assert result == "Test Workflow"

    # Verify all executions are deleted
    assert mock_workflow_execution_delete.call_count == 2
    mock_workflow_execution_delete.assert_any_call("exec-1")
    mock_workflow_execution_delete.assert_any_call("exec-2")

    # Verify workflow is deleted
    mock_workflow_config_delete.assert_called_once_with("workflow-123")


@patch("codemie.rest_api.models.settings.Settings.get_by_id")
@patch("codemie.service.aws_bedrock.bedrock_flow_service.get_setting_aws_credentials")
@patch("codemie.service.aws_bedrock.bedrock_flow_service.BedrockFlowService._bedrock_get_flow_alias")
def test_validate_remote_entity_exists_and_cleanup_other_client_error_passes(
    mock_bedrock_get_flow_alias,
    mock_get_setting_aws_credentials,
    mock_settings_get_by_id,
    mock_aws_creds,
):
    """Test validate_remote_entity_exists_and_cleanup with other AWS client errors."""
    from botocore.exceptions import ClientError

    # Create mock workflow config with Bedrock configuration
    mock_workflow = MagicMock()
    mock_workflow.bedrock.bedrock_flow_id = "flow-123"
    mock_workflow.bedrock.bedrock_flow_alias_id = "alias-456"
    mock_workflow.bedrock.bedrock_aws_settings_id = "setting-789"
    mock_workflow.name = "Test Workflow"

    mock_setting = MagicMock()
    mock_settings_get_by_id.return_value = mock_setting
    mock_get_setting_aws_credentials.return_value = mock_aws_creds

    # Mock other client error
    error_response = {"Error": {"Code": "AccessDeniedException", "Message": "Access denied"}}
    mock_bedrock_get_flow_alias.side_effect = ClientError(error_response, "GetFlowAlias")

    BedrockFlowService.validate_remote_entity_exists_and_cleanup(mock_workflow)

    mock_workflow.delete.assert_not_called()


def test_validate_remote_entity_exists_and_cleanup_non_bedrock_workflow():
    """Test validate_remote_entity_exists_and_cleanup with non-Bedrock workflow."""
    # Create mock workflow without Bedrock configuration
    mock_workflow = MagicMock()
    mock_workflow.bedrock = None

    result = BedrockFlowService.validate_remote_entity_exists_and_cleanup(mock_workflow)

    assert result is None


def test_validate_remote_entity_exists_and_cleanup_missing_bedrock_fields():
    """Test validate_remote_entity_exists_and_cleanup with incomplete Bedrock configuration."""
    # Create mock workflow with incomplete Bedrock configuration
    mock_workflow = MagicMock()
    mock_workflow.bedrock.bedrock_flow_id = None
    mock_workflow.bedrock.bedrock_flow_alias_id = "alias-456"

    result = BedrockFlowService.validate_remote_entity_exists_and_cleanup(mock_workflow)

    assert result is None


@patch("codemie.rest_api.models.settings.Settings.get_by_id")
def test_validate_remote_entity_exists_and_cleanup_setting_not_found_passes(
    mock_settings_get_by_id,
):
    """Test validate_remote_entity_exists_and_cleanup when setting is not found."""
    # Create mock workflow config with Bedrock configuration
    mock_workflow = MagicMock()
    mock_workflow.bedrock.bedrock_flow_id = "flow-123"
    mock_workflow.bedrock.bedrock_flow_alias_id = "alias-456"
    mock_workflow.bedrock.bedrock_aws_settings_id = "setting-789"

    mock_settings_get_by_id.return_value = None

    BedrockFlowService.validate_remote_entity_exists_and_cleanup(mock_workflow)

    mock_workflow.delete.assert_not_called()


@patch("codemie.rest_api.models.settings.Settings.get_by_id")
def test_validate_remote_entity_exists_and_cleanup_unexpected_error_passes(
    mock_settings_get_by_id,
):
    """Test validate_remote_entity_exists_and_cleanup with unexpected errors."""
    # Create mock workflow config with Bedrock configuration
    mock_workflow = MagicMock()
    mock_workflow.bedrock.bedrock_flow_id = "flow-123"
    mock_workflow.bedrock.bedrock_flow_alias_id = "alias-456"
    mock_workflow.bedrock.bedrock_aws_settings_id = "setting-789"
    mock_workflow.name = "Test Workflow"

    mock_settings_get_by_id.side_effect = Exception("Unexpected error")

    BedrockFlowService.validate_remote_entity_exists_and_cleanup(mock_workflow)
