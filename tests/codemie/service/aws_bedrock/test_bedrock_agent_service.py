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
from codemie.core.exceptions import ExtendedHTTPException
from codemie.core.models import AssistantChatRequest
from codemie.rest_api.models.assistant import Assistant
from codemie.rest_api.models.vendor import ImportAgent
from codemie.service.aws_bedrock.bedrock_agent_service import BedrockAgentService
from codemie.rest_api.models.settings import AWSCredentials, Settings, SettingsBase
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
def agent_data():
    return [
        {
            "agentId": "agent-1",
            "agentName": "Agent 1",
            "agentStatus": "PREPARED",
            "latestAgentVersion": "1",
            "updatedAt": "2024-01-01T00:00:00Z",
        },
        {
            "agentId": "agent-2",
            "agentName": "Agent 2",
            "agentStatus": "NOT_PREPARED",
            "latestAgentVersion": "2",
            "updatedAt": "2024-01-02T00:00:00Z",
        },
    ]


@pytest.fixture
def agent_aliases():
    return [
        {
            "agentAliasId": "alias-1",
            "agentAliasName": "Alias 1",
            "agentAliasStatus": "PREPARED",
            "aliasInvocationState": "ACCEPT_INVOCATIONS",
            "routingConfiguration": [{"agentVersion": "1"}],
            "description": "Test alias 1",
            "createdAt": "2024-01-01T00:00:00Z",
            "updatedAt": "2024-01-01T00:00:00Z",
        },
        {
            "agentAliasId": "alias-2",
            "agentAliasName": "Alias 2",
            "agentAliasStatus": "NOT_READY",
            "aliasInvocationState": "ACCEPT_INVOCATIONS",
            "routingConfiguration": [{"agentVersion": "2"}],
            "description": "Test alias 2",
            "createdAt": "2024-01-02T00:00:00Z",
            "updatedAt": "2024-01-02T00:00:00Z",
        },
        {
            "agentAliasId": "alias-draft",
            "agentAliasName": "Alias Draft",
            "agentAliasStatus": "PREPARED",
            "aliasInvocationState": "ACCEPT_INVOCATIONS",
            "routingConfiguration": [{"agentVersion": "DRAFT"}],
            "description": "Draft alias",
            "createdAt": "2024-01-01T00:00:00Z",
            "updatedAt": "2024-01-01T00:00:00Z",
        },
        {
            "agentAliasId": "alias-norc",
            "agentAliasName": "Alias NoRC",
            "agentAliasStatus": "PREPARED",
            "aliasInvocationState": "ACCEPT_INVOCATIONS",
            # No routingConfiguration
            "description": "No routing config alias",
            "createdAt": "2024-01-01T00:00:00Z",
            "updatedAt": "2024-01-01T00:00:00Z",
        },
    ]


@pytest.fixture
def agent_version():
    return {
        "agentId": "agent-1",
        "agentName": "Agent 1",
        "agentArn": "arn:aws:bedrock:us-east-1:123456789012:agent/agent-1",
        "version": "1",
        "description": "Test agent description",
        "foundationModel": "anthropic.claude-3-sonnet-20240229-v1:0",
        "instruction": "Test instruction",
        "agentStatus": "PREPARED",
        "createdAt": "2024-01-01T00:00:00Z",
        "updatedAt": "2024-01-01T00:00:00Z",
    }


@pytest.fixture
def mock_assistant():
    """Fixture to create a mock Assistant object."""
    assistant = MagicMock(spec=Assistant)
    assistant.id = "assistant-id"

    bedrock = MagicMock()
    bedrock.bedrock_agent_id = "test-agent-id"
    bedrock.bedrock_agent_alias_id = "test-alias-id"
    bedrock.bedrock_agent_name = "Assistant"
    bedrock.bedrock_agent_description = None
    bedrock.bedrock_agent_version = "1"
    bedrock.bedrock_aws_settings_id = "setting-1"
    assistant.bedrock = bedrock

    assistant.toolkits = [
        MagicMock(
            toolkit="AWS",
            settings=MagicMock(
                id="setting-1",
                region="us-east-1",
                access_key_id="test-access-key",
                secret_access_key="test-secret-key",
            ),
        )
    ]
    assistant.created_by = MagicMock(id="user-id", name="Test User")
    assistant.project = "test-project"
    return assistant


@pytest.fixture
def mock_request():
    """Fixture to create a mock AssistantChatRequest object."""
    request = MagicMock(spec=AssistantChatRequest)
    request.text = "Test input text"
    request.conversation_id = "test-conversation-id"
    request.history = []
    return request


# --- Tests for get_all_settings_overview ---
@patch("codemie.service.aws_bedrock.bedrock_agent_service.get_all_settings_for_user")
@patch("codemie.service.aws_bedrock.bedrock_agent_service.BedrockAgentService._fetch_main_entity_names_for_setting")
def test_get_all_settings_overview_success(
    mock_fetch_main_entity_names_for_setting,
    mock_get_all_settings_for_user,
    mock_user,
    agent_data,
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

    # Mock the fetch method to return agent names for different settings
    mock_fetch_main_entity_names_for_setting.side_effect = [
        ["Agent 1"],  # For setting-1
        ["Agent 2"],  # For setting-2
    ]

    result = BedrockAgentService.get_all_settings_overview(mock_user, page=0, per_page=10)

    # Assertions
    assert "data" in result
    assert "pagination" in result
    assert len(result["data"]) == 2

    # Check first setting
    setting1_data = result["data"][0]
    assert setting1_data["setting_id"] == "setting-1"
    assert setting1_data["setting_name"] == "Setting 1"
    assert setting1_data["project"] == "project-1"
    assert setting1_data["entities"] == ["Agent 1"]

    # Check second setting
    setting2_data = result["data"][1]
    assert setting2_data["setting_id"] == "setting-2"
    assert setting2_data["setting_name"] == "Setting 2"
    assert setting2_data["project"] == "project-2"
    assert setting2_data["entities"] == ["Agent 2"]


@patch("codemie.service.aws_bedrock.bedrock_agent_service.get_all_settings_for_user")
@patch("codemie.service.aws_bedrock.bedrock_agent_service.BedrockAgentService._fetch_main_entity_names_for_setting")
def test_get_all_settings_overview_empty_settings(
    mock_fetch_main_entity_names_for_setting,
    mock_get_all_settings_for_user,
    mock_user,
):
    """Test get_all_settings_overview with no settings available."""
    mock_get_all_settings_for_user.return_value = []

    result = BedrockAgentService.get_all_settings_overview(mock_user, page=0, per_page=10)

    assert result["data"] == []
    assert result["pagination"]["total"] == 0
    assert result["pagination"]["pages"] == 0
    assert result["pagination"]["page"] == 0
    assert result["pagination"]["per_page"] == 10

    # Should not call the fetch method when there are no settings
    mock_fetch_main_entity_names_for_setting.assert_not_called()


@patch("codemie.service.aws_bedrock.bedrock_agent_service.get_all_settings_for_user")
@patch("codemie.service.aws_bedrock.bedrock_agent_service.BedrockAgentService._fetch_main_entity_names_for_setting")
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
    mock_fetch_main_entity_names_for_setting.return_value = ["Agent Test"]

    # Test first page (page=0, per_page=2)
    result = BedrockAgentService.get_all_settings_overview(mock_user, page=0, per_page=2)

    assert len(result["data"]) == 2
    assert result["pagination"]["total"] == 5
    assert result["pagination"]["pages"] == 3  # ceil(5/2)
    assert result["pagination"]["page"] == 0
    assert result["pagination"]["per_page"] == 2
    assert result["data"][0]["setting_id"] == "setting-0"
    assert result["data"][1]["setting_id"] == "setting-1"


@patch("codemie.service.aws_bedrock.bedrock_agent_service.get_all_settings_for_user")
@patch("codemie.service.aws_bedrock.bedrock_agent_service.BedrockAgentService._fetch_main_entity_names_for_setting")
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
    result = BedrockAgentService.get_all_settings_overview(mock_user, page=0, per_page=10)
    assert len(result["data"]) == 1
    assert result["data"][0]["setting_id"] == "setting-1"
    assert result["data"][0]["error"] == "AWS error"
    assert result["data"][0]["invalid"] is False


@patch("codemie.service.aws_bedrock.bedrock_agent_service.get_all_settings_for_user")
@patch("codemie.service.aws_bedrock.bedrock_agent_service.get_setting_aws_credentials")
@patch("codemie.service.aws_bedrock.bedrock_agent_service.BedrockAgentService._bedrock_list_all_agents")
def test_get_all_settings_overview_limits_entity_count(
    mock_bedrock_list_all_agents,
    mock_get_setting_aws_credentials,
    mock_get_all_settings_for_user,
    mock_user,
    mock_setting,
    mock_aws_creds,
):
    """Test get_all_settings_overview limits entity count to ALL_SETTINGS_OVERVIEW_ENTITY_COUNT."""
    mock_get_all_settings_for_user.return_value = [mock_setting]
    mock_get_setting_aws_credentials.return_value = mock_aws_creds

    # Create more than 4 agents
    many_agents = []
    for i in range(10):
        many_agents.append({"agentName": f"Agent {i}"})

    mock_bedrock_list_all_agents.return_value = many_agents, None

    result = BedrockAgentService.get_all_settings_overview(mock_user, page=0, per_page=10)

    # Should limit to 4 agents
    setting_data = result["data"][0]
    assert len(setting_data["entities"]) == 4


# --- Tests for list_main_entities ---
@patch("codemie.service.aws_bedrock.bedrock_agent_service.get_setting_for_user")
@patch("codemie.service.aws_bedrock.bedrock_agent_service.get_setting_aws_credentials")
@patch("codemie.service.aws_bedrock.bedrock_agent_service.BedrockAgentService._bedrock_list_all_agents")
def test_list_main_entities_success(
    mock_bedrock_list_all_agents,
    mock_get_setting_aws_credentials,
    mock_get_setting_for_user,
    mock_user,
    mock_setting,
    mock_aws_creds,
    agent_data,
):
    """Test list_main_entities returns correct agent data."""
    mock_get_setting_for_user.return_value = mock_setting
    mock_get_setting_aws_credentials.return_value = mock_aws_creds
    mock_bedrock_list_all_agents.return_value = (agent_data, None)

    result, next_token = BedrockAgentService.list_main_entities(mock_user, "setting-1", page=0, per_page=10)

    assert len(result) == 2
    assert result[0]["id"] == "agent-1"
    assert result[0]["name"] == "Agent 1"
    assert result[0]["status"] == "PREPARED"
    assert result[1]["id"] == "agent-2"
    assert result[1]["name"] == "Agent 2"
    assert result[1]["status"] == "NOT_PREPARED"
    assert next_token is None


@patch("codemie.service.aws_bedrock.bedrock_agent_service.get_setting_for_user")
@patch("codemie.service.aws_bedrock.bedrock_agent_service.get_setting_aws_credentials")
@patch("codemie.service.aws_bedrock.bedrock_agent_service.BedrockAgentService._bedrock_list_all_agents")
def test_list_main_entities_empty(
    mock_bedrock_list_all_agents,
    mock_get_setting_aws_credentials,
    mock_get_setting_for_user,
    mock_user,
    mock_setting,
    mock_aws_creds,
):
    """Test list_main_entities with no agents."""
    mock_get_setting_for_user.return_value = mock_setting
    mock_get_setting_aws_credentials.return_value = mock_aws_creds
    mock_bedrock_list_all_agents.return_value = ([], None)

    result, next_token = BedrockAgentService.list_main_entities(mock_user, "setting-1", page=0, per_page=10)

    assert result == []
    assert next_token is None


# --- Tests for get_main_entity_detail ---
@patch("codemie.service.aws_bedrock.bedrock_agent_service.get_setting_for_user")
@patch("codemie.service.aws_bedrock.bedrock_agent_service.get_setting_aws_credentials")
@patch("codemie.service.aws_bedrock.bedrock_agent_service.BedrockAgentService._bedrock_get_agent")
def test_get_main_entity_detail_success(
    mock_bedrock_get_agent,
    mock_get_setting_aws_credentials,
    mock_get_setting_for_user,
    mock_user,
    mock_setting,
    mock_aws_creds,
):
    """Test get_main_entity_detail returns correct agent information."""
    mock_get_setting_for_user.return_value = mock_setting
    mock_get_setting_aws_credentials.return_value = mock_aws_creds

    agent_detail = {
        "agentId": "agent-1",
        "agentName": "Agent 1",
        "agentArn": "arn:aws:bedrock:us-east-1:123456789012:agent/agent-1",
        "agentVersion": "DRAFT",
        "description": "Test agent description",
        "foundationModel": "anthropic.claude-3-sonnet-20240229-v1:0",
        "instruction": "Test instruction",
        "agentStatus": "PREPARED",
        "createdAt": "2024-01-01T00:00:00Z",
        "updatedAt": "2024-01-01T00:00:00Z",
    }
    mock_bedrock_get_agent.return_value = agent_detail

    result = BedrockAgentService.get_main_entity_detail(mock_user, "agent-1", "setting-1")

    assert result["id"] == "agent-1"
    assert result["name"] == "Agent 1"
    assert result["description"] == "Test agent description"
    assert result["status"] == "PREPARED"

    mock_get_setting_for_user.assert_called_once_with(mock_user, "setting-1")
    mock_get_setting_aws_credentials.assert_called_once_with(mock_setting.id)
    mock_bedrock_get_agent.assert_called_once_with(
        agent_id="agent-1",
        region=mock_aws_creds.region,
        access_key_id=mock_aws_creds.access_key_id,
        secret_access_key=mock_aws_creds.secret_access_key,
        session_token=mock_aws_creds.session_token,
    )


@patch("codemie.service.aws_bedrock.bedrock_agent_service.get_setting_for_user")
@patch("codemie.service.aws_bedrock.bedrock_agent_service.get_setting_aws_credentials")
@patch("codemie.service.aws_bedrock.bedrock_agent_service.BedrockAgentService._bedrock_get_agent")
def test_get_main_entity_detail_not_found(
    mock_bedrock_get_agent,
    mock_get_setting_aws_credentials,
    mock_get_setting_for_user,
    mock_user,
    mock_setting,
    mock_aws_creds,
):
    """Test get_main_entity_detail when agent is not found."""
    mock_get_setting_for_user.return_value = mock_setting
    mock_get_setting_aws_credentials.return_value = mock_aws_creds
    mock_bedrock_get_agent.return_value = None

    with pytest.raises(ExtendedHTTPException):
        BedrockAgentService.get_main_entity_detail(mock_user, "agent-1", "setting-1")


# --- Tests for list_importable_entities_for_main_entity ---
@patch("codemie.service.aws_bedrock.bedrock_agent_service.get_setting_for_user")
@patch("codemie.service.aws_bedrock.bedrock_agent_service.get_setting_aws_credentials")
@patch("codemie.service.aws_bedrock.bedrock_agent_service.Assistant.get_by_bedrock_aws_settings_id")
@patch("codemie.service.aws_bedrock.bedrock_agent_service.BedrockAgentService._bedrock_list_agent_aliases")
def test_list_importable_entities_for_main_entity_success(
    mock_list_agent_aliases,
    mock_get_by_bedrock_aws_settings_id,
    mock_get_setting_aws_credentials,
    mock_get_setting_for_user,
    mock_user,
    mock_setting,
    mock_aws_creds,
    agent_aliases,
):
    """Test list_importable_entities_for_main_entity returns correct alias data."""
    mock_get_setting_for_user.return_value = mock_setting
    mock_get_setting_aws_credentials.return_value = mock_aws_creds
    mock_get_by_bedrock_aws_settings_id.return_value = []
    mock_list_agent_aliases.return_value = (agent_aliases, None)

    result, next_token = BedrockAgentService.list_importable_entities_for_main_entity(
        mock_user, "agent-1", "setting-1", page=0, per_page=10
    )

    assert len(result) == 4
    assert next_token is None

    # Check first alias (should be PREPARED)
    assert result[0]["id"] == "alias-1"
    assert result[0]["name"] == "Alias 1"
    assert result[0]["status"] == "PREPARED"
    assert result[0]["version"] == "1"

    # Check second alias (should be NOT_PREPARED due to status)
    assert result[1]["id"] == "alias-2"
    assert result[1]["status"] == "NOT_PREPARED"

    # Check draft alias (should be NOT_PREPARED due to DRAFT version)
    assert result[2]["id"] == "alias-draft"
    assert result[2]["status"] == "NOT_PREPARED"

    # Check no routing config alias (should have None version)
    assert result[3]["id"] == "alias-norc"
    assert result[3]["version"] is None


@patch("codemie.service.aws_bedrock.bedrock_agent_service.get_setting_for_user")
@patch("codemie.service.aws_bedrock.bedrock_agent_service.get_setting_aws_credentials")
@patch("codemie.service.aws_bedrock.bedrock_agent_service.Assistant.get_by_bedrock_aws_settings_id")
@patch("codemie.service.aws_bedrock.bedrock_agent_service.BedrockAgentService._bedrock_list_agent_aliases")
def test_list_importable_entities_for_main_entity_with_existing_entities(
    mock_list_agent_aliases,
    mock_get_by_bedrock_aws_settings_id,
    mock_get_setting_aws_credentials,
    mock_get_setting_for_user,
    mock_user,
    mock_setting,
    mock_aws_creds,
    agent_aliases,
):
    """Test list_importable_entities_for_main_entity includes aiRunId for existing entities."""
    mock_get_setting_for_user.return_value = mock_setting
    mock_get_setting_aws_credentials.return_value = mock_aws_creds

    # Mock existing entity
    existing_assistant = MagicMock()
    existing_assistant.id = "assistant-123"
    existing_assistant.bedrock.bedrock_agent_alias_id = "alias-1"
    mock_get_by_bedrock_aws_settings_id.return_value = [existing_assistant]

    mock_list_agent_aliases.return_value = ([agent_aliases[0]], None)  # Only first alias

    result, next_token = BedrockAgentService.list_importable_entities_for_main_entity(
        mock_user, "agent-1", "setting-1", page=0, per_page=10
    )

    assert len(result) == 1
    assert result[0]["id"] == "alias-1"
    assert result[0]["aiRunId"] == "assistant-123"
    assert next_token is None


@patch("codemie.service.aws_bedrock.bedrock_agent_service.get_setting_for_user")
@patch("codemie.service.aws_bedrock.bedrock_agent_service.get_setting_aws_credentials")
@patch("codemie.service.aws_bedrock.bedrock_agent_service.Assistant.get_by_bedrock_aws_settings_id")
@patch("codemie.service.aws_bedrock.bedrock_agent_service.BedrockAgentService._bedrock_list_agent_aliases")
def test_list_importable_entities_for_main_entity_handles_exceptions(
    mock_list_agent_aliases,
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
    mock_list_agent_aliases.side_effect = Exception("AWS error")

    with pytest.raises(ExtendedHTTPException):
        BedrockAgentService.list_importable_entities_for_main_entity(
            mock_user, "agent-1", "setting-1", page=0, per_page=10
        )


# --- Tests for get_importable_entity_detail ---
@patch("codemie.service.aws_bedrock.bedrock_agent_service.get_setting_for_user")
@patch("codemie.service.aws_bedrock.bedrock_agent_service.get_setting_aws_credentials")
@patch("codemie.service.aws_bedrock.bedrock_agent_service.BedrockAgentService._bedrock_get_agent_version")
def test_get_importable_entity_detail_success(
    mock_get_agent_version,
    mock_get_setting_aws_credentials,
    mock_get_setting_for_user,
    mock_user,
    mock_setting,
    mock_aws_creds,
    agent_version,
):
    """Test get_importable_entity_detail returns correct version information."""
    mock_get_setting_for_user.return_value = mock_setting
    mock_get_setting_aws_credentials.return_value = mock_aws_creds
    mock_get_agent_version.return_value = agent_version

    result = BedrockAgentService.get_importable_entity_detail(mock_user, "agent-1", "1", "setting-1")

    assert result["id"] == "agent-1"
    assert result["name"] == "Agent 1"
    assert result["version"] == "1"
    assert result["description"] == "Test agent description"
    assert result["foundationModel"] == "anthropic.claude-3-sonnet-20240229-v1:0"


@patch("codemie.service.aws_bedrock.bedrock_agent_service.get_setting_for_user")
@patch("codemie.service.aws_bedrock.bedrock_agent_service.get_setting_aws_credentials")
@patch("codemie.service.aws_bedrock.bedrock_agent_service.BedrockAgentService._bedrock_get_agent_version")
def test_get_importable_entity_detail_not_found(
    mock_get_agent_version,
    mock_get_setting_aws_credentials,
    mock_get_setting_for_user,
    mock_user,
    mock_setting,
    mock_aws_creds,
):
    """Test get_importable_entity_detail when version info is not found."""
    mock_get_setting_for_user.return_value = mock_setting
    mock_get_setting_aws_credentials.return_value = mock_aws_creds
    mock_get_agent_version.return_value = None

    result = BedrockAgentService.get_importable_entity_detail(mock_user, "agent-1", "1", "setting-1")

    assert result == {}


# --- Tests for import_entities ---
@patch("codemie.service.aws_bedrock.bedrock_agent_service.get_setting_for_user")
@patch("codemie.service.aws_bedrock.bedrock_agent_service.get_setting_aws_credentials")
@patch("codemie.service.aws_bedrock.bedrock_agent_service.Assistant.get_by_bedrock_aws_settings_id")
@patch("codemie.service.aws_bedrock.bedrock_agent_service.BedrockAgentService._process_alias_import")
def test_import_entities_success(
    mock_process_alias_import,
    mock_get_by_bedrock_aws_settings_id,
    mock_get_setting_aws_credentials,
    mock_get_setting_for_user,
    mock_user,
):
    """Test import_entities calls _process_alias_import correctly."""
    setting_id = "setting-1"
    import_payload = {
        setting_id: [
            ImportAgent(id="agent-1", agentAliasId="alias-1", setting_id=setting_id),
            ImportAgent(id="agent-2", agentAliasId="alias-2", setting_id=setting_id),
        ]
    }
    mock_setting = MagicMock(spec=SettingsBase)
    mock_setting.id = setting_id
    mock_get_setting_for_user.return_value = mock_setting
    mock_get_setting_aws_credentials.return_value = MagicMock(spec=AWSCredentials)
    mock_get_by_bedrock_aws_settings_id.return_value = []
    mock_process_alias_import.side_effect = [
        {"agentId": "agent-1", "agentAliasId": "alias-1", "aiRunId": "assistant-1"},
        {"agentId": "agent-2", "agentAliasId": "alias-2", "aiRunId": "assistant-2"},
    ]

    result = BedrockAgentService.import_entities(mock_user, import_payload)

    assert len(result) == 2
    assert result[0]["agentId"] == "agent-1"
    assert result[1]["agentId"] == "agent-2"
    assert mock_process_alias_import.call_count == 2


# --- Tests for invoke_agent ---
@patch("codemie.service.aws_bedrock.utils.SettingsService.get_aws_creds")
@patch("codemie.service.aws_bedrock.bedrock_agent_service.BedrockAgentService._bedrock_invoke_agent")
def test_invoke_agent_success(mock_bedrock_invoke_agent, mock_get_aws_creds, mock_assistant, mock_request):
    """Test invoke_agent calls AWS and returns the expected output."""
    mock_get_aws_creds.return_value = AWSCredentials(
        region="us-east-1",
        access_key_id="test-access-key",
        secret_access_key="test-secret-key",
    )
    mock_bedrock_invoke_agent.return_value = "Test response from Bedrock agent"

    response = BedrockAgentService.invoke_agent(
        assistant=mock_assistant,
        input_text=mock_request.text,
        conversation_id=mock_request.conversation_id,
        chat_history=mock_request.history,
    )

    assert response["output"] == "Test response from Bedrock agent"
    assert "time_elapsed" in response
    mock_bedrock_invoke_agent.assert_called_once()


# --- Tests for delete_entities ---
@patch("codemie.service.aws_bedrock.bedrock_agent_service.Assistant.get_by_bedrock_aws_settings_id")
@patch("codemie.service.guardrail.guardrail_service.GuardrailService.remove_guardrail_assignments_for_entity")
def test_delete_entities_deletes_all_assistants(mock_remove_guardrails, mock_get_by_bedrock_aws_settings_id):
    """Test delete_entities deletes all assistants for a given setting_id."""
    mock_remove_guardrails.return_value = None

    mock_assistant1 = MagicMock()
    mock_assistant2 = MagicMock()
    mock_get_by_bedrock_aws_settings_id.return_value = [mock_assistant1, mock_assistant2]

    BedrockAgentService.delete_entities("setting-1")

    mock_assistant1.delete.assert_called_once()
    mock_assistant2.delete.assert_called_once()
    mock_get_by_bedrock_aws_settings_id.assert_called_once_with("setting-1")


# --- Tests for validate_remote_entity_exists_and_cleanup ---
@patch("codemie.service.aws_bedrock.utils.SettingsService.get_aws_creds")
@patch("codemie.service.aws_bedrock.bedrock_agent_service.BedrockAgentService._bedrock_get_agent_alias")
def test_validate_remote_entity_exists_and_cleanup_success(
    mock_bedrock_get_agent_alias,
    mock_get_aws_creds,
    mock_aws_creds,
):
    """Test validate_remote_entity_exists_and_cleanup when agent alias exists remotely."""
    # Create mock assistant with Bedrock configuration
    mock_assistant = MagicMock()
    mock_assistant.type = "bedrock_agent"
    mock_assistant.bedrock.bedrock_agent_id = "agent-123"
    mock_assistant.bedrock.bedrock_agent_alias_id = "alias-456"
    mock_assistant.bedrock.bedrock_aws_settings_id = "123"
    mock_assistant.name = "Test Assistant"

    mock_get_aws_creds.return_value = mock_aws_creds
    mock_bedrock_get_agent_alias.return_value = {"agentAliasId": "alias-456"}

    result = BedrockAgentService.validate_remote_entity_exists_and_cleanup(mock_assistant)

    assert result is None
    mock_get_aws_creds.assert_called_once_with(integration_id=mock_assistant.bedrock.bedrock_aws_settings_id)
    mock_bedrock_get_agent_alias.assert_called_once_with(
        agent_id="agent-123",
        agent_alias_id="alias-456",
        region=mock_aws_creds.region,
        access_key_id=mock_aws_creds.access_key_id,
        secret_access_key=mock_aws_creds.secret_access_key,
        session_token=mock_aws_creds.session_token,
    )
    mock_assistant.delete.assert_not_called()


@patch("codemie.service.aws_bedrock.utils.SettingsService.get_aws_creds")
@patch("codemie.service.aws_bedrock.bedrock_agent_service.BedrockAgentService._bedrock_get_agent_alias")
@patch("codemie.service.guardrail.guardrail_service.GuardrailService.remove_guardrail_assignments_for_entity")
def test_validate_remote_entity_exists_and_cleanup_resource_not_found(
    mock_remove_guardrails,
    mock_bedrock_get_agent_alias,
    mock_get_aws_creds,
    mock_aws_creds,
):
    """Test validate_remote_entity_exists_and_cleanup when agent alias is deleted remotely."""
    from botocore.exceptions import ClientError

    mock_remove_guardrails.return_value = None

    # Create mock assistant with Bedrock configuration
    mock_assistant = MagicMock()
    mock_assistant.type = "bedrock_agent"
    mock_assistant.bedrock.bedrock_agent_id = "agent-123"
    mock_assistant.bedrock.bedrock_agent_alias_id = "alias-456"
    mock_assistant.name = "Test Assistant"

    mock_get_aws_creds.return_value = mock_aws_creds

    # Mock ResourceNotFoundException
    error_response = {"Error": {"Code": "ResourceNotFoundException", "Message": "Agent alias not found"}}
    mock_bedrock_get_agent_alias.side_effect = ClientError(error_response, "GetAgentAlias")

    result = BedrockAgentService.validate_remote_entity_exists_and_cleanup(mock_assistant)

    assert result == "Test Assistant"
    mock_assistant.delete.assert_called_once()


@patch("codemie.service.aws_bedrock.utils.SettingsService.get_aws_creds")
@patch("codemie.service.aws_bedrock.bedrock_agent_service.BedrockAgentService._bedrock_get_agent_alias")
def test_validate_remote_entity_exists_and_cleanup_other_client_error_passes(
    mock_bedrock_get_agent_alias,
    mock_get_aws_creds,
    mock_aws_creds,
):
    """Test validate_remote_entity_exists_and_cleanup with other AWS client errors."""
    from botocore.exceptions import ClientError

    # Create mock assistant with Bedrock configuration
    mock_assistant = MagicMock()
    mock_assistant.type = "bedrock_agent"
    mock_assistant.bedrock.bedrock_agent_id = "agent-123"
    mock_assistant.bedrock.bedrock_agent_alias_id = "alias-456"
    mock_assistant.name = "Test Assistant"

    mock_get_aws_creds.return_value = mock_aws_creds

    # Mock other client error
    error_response = {"Error": {"Code": "AccessDeniedException", "Message": "Access denied"}}
    mock_bedrock_get_agent_alias.side_effect = ClientError(error_response, "GetAgentAlias")

    BedrockAgentService.validate_remote_entity_exists_and_cleanup(mock_assistant)

    mock_assistant.delete.assert_not_called()


def test_validate_remote_entity_exists_and_cleanup_non_bedrock_assistant():
    """Test validate_remote_entity_exists_and_cleanup with non-Bedrock assistant."""
    # Create mock assistant without Bedrock configuration
    mock_assistant = MagicMock()
    mock_assistant.type = "openai"
    mock_assistant.bedrock = None

    result = BedrockAgentService.validate_remote_entity_exists_and_cleanup(mock_assistant)

    assert result is None
    mock_assistant.delete.assert_not_called()


def test_validate_remote_entity_exists_and_cleanup_missing_bedrock_fields():
    """Test validate_remote_entity_exists_and_cleanup with incomplete Bedrock configuration."""
    # Create mock assistant with incomplete Bedrock configuration
    mock_assistant = MagicMock()
    mock_assistant.type = "bedrock_agent"
    mock_assistant.bedrock.bedrock_agent_id = None
    mock_assistant.bedrock.bedrock_agent_alias_id = "alias-456"

    result = BedrockAgentService.validate_remote_entity_exists_and_cleanup(mock_assistant)

    assert result is None
    mock_assistant.delete.assert_not_called()


@patch("codemie.service.aws_bedrock.utils.SettingsService.get_aws_creds")
def test_validate_remote_entity_exists_and_cleanup_unexpected_error_passes(
    mock_get_aws_creds,
):
    """Test validate_remote_entity_exists_and_cleanup with unexpected errors."""
    # Create mock assistant with Bedrock configuration
    mock_assistant = MagicMock()
    mock_assistant.type = "bedrock_agent"
    mock_assistant.bedrock.bedrock_agent_id = "agent-123"
    mock_assistant.bedrock.bedrock_agent_alias_id = "alias-456"
    mock_assistant.name = "Test Assistant"

    mock_get_aws_creds.side_effect = Exception("Unexpected error")

    BedrockAgentService.validate_remote_entity_exists_and_cleanup(mock_assistant)

    mock_assistant.delete.assert_not_called()
