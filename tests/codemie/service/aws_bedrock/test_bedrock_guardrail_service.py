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
from codemie.service.aws_bedrock.bedrock_guardrail_service import BedrockGuardrailService
from codemie.rest_api.models.settings import AWSCredentials, SettingsBase
from codemie.rest_api.security.user import User
from codemie.rest_api.models.guardrail import Guardrail
from codemie.rest_api.models.vendor import ImportGuardrail
from codemie.core.models import CreatedByUser
from codemie.core.exceptions import ExtendedHTTPException


@pytest.fixture
def mock_user():
    user = MagicMock(spec=User)
    user.id = "user-id"
    user.username = "testuser"
    user.name = "Test User"
    user.project_names = ["proj1"]
    user.admin_project_names = []
    user.is_admin = False
    return user


@pytest.fixture
def mock_setting():
    setting = MagicMock(spec=SettingsBase)
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
def guardrails_response():
    return [
        {"id": "g1", "status": "READY", "name": "Guardrail 1", "description": "desc1", "version": "1"},
        {"id": "g2", "status": "NOT_READY", "name": "Guardrail 2", "description": "desc2", "version": "2"},
        {"id": "g3", "status": "READY", "name": "Guardrail 3", "description": "desc3", "version": "3"},
    ]


@pytest.fixture
def import_guardrail():
    return ImportGuardrail(id="g1", version="1", setting_id="setting-1")


@pytest.fixture
def mock_guardrail_obj():
    guardrail = MagicMock(spec=Guardrail)
    guardrail.id = "g1"
    guardrail.bedrock = MagicMock()
    guardrail.bedrock.bedrock_guardrail_id = "g1"
    guardrail.bedrock.bedrock_version = "1"
    guardrail.bedrock.bedrock_name = "Guardrail 1"
    guardrail.bedrock.bedrock_aws_settings_id = "setting-1"
    guardrail.description = "desc1"
    guardrail.created_by = CreatedByUser(id="user-id", username="testuser", name="Test User")
    return guardrail


# --- Tests for get_all_settings_overview ---
@patch("codemie.service.aws_bedrock.bedrock_guardrail_service.get_all_settings_for_user")
@patch(
    "codemie.service.aws_bedrock.bedrock_guardrail_service.BedrockGuardrailService._fetch_main_entity_names_for_setting"
)
def test_get_all_settings_overview_success(
    mock_fetch_main_entity_names_for_setting,
    mock_get_all_settings_for_user,
    mock_user,
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

    # Mock the fetch method to return guardrail names for different settings
    mock_fetch_main_entity_names_for_setting.side_effect = [
        ["Guardrail 1"],  # For setting-1
        ["Guardrail 2"],  # For setting-2
    ]

    result = BedrockGuardrailService.get_all_settings_overview(mock_user, page=0, per_page=10)

    # Assertions
    assert "data" in result
    assert "pagination" in result
    assert len(result["data"]) == 2

    # Check first setting
    setting1_data = result["data"][0]
    assert setting1_data["setting_id"] == "setting-1"
    assert setting1_data["setting_name"] == "Setting 1"
    assert setting1_data["project"] == "project-1"
    assert setting1_data["entities"] == ["Guardrail 1"]

    # Check second setting
    setting2_data = result["data"][1]
    assert setting2_data["setting_id"] == "setting-2"
    assert setting2_data["setting_name"] == "Setting 2"
    assert setting2_data["project"] == "project-2"
    assert setting2_data["entities"] == ["Guardrail 2"]


@patch("codemie.service.aws_bedrock.bedrock_guardrail_service.get_all_settings_for_user")
@patch(
    "codemie.service.aws_bedrock.bedrock_guardrail_service.BedrockGuardrailService._fetch_main_entity_names_for_setting"
)
def test_get_all_settings_overview_empty_settings(
    mock_fetch_main_entity_names_for_setting,
    mock_get_all_settings_for_user,
    mock_user,
):
    """Test get_all_settings_overview with no settings available."""
    mock_get_all_settings_for_user.return_value = []

    result = BedrockGuardrailService.get_all_settings_overview(mock_user, page=0, per_page=10)

    assert result["data"] == []
    assert result["pagination"]["total"] == 0
    assert result["pagination"]["pages"] == 0
    assert result["pagination"]["page"] == 0
    assert result["pagination"]["per_page"] == 10

    # Should not call the fetch method when there are no settings
    mock_fetch_main_entity_names_for_setting.assert_not_called()


@patch("codemie.service.aws_bedrock.bedrock_guardrail_service.get_all_settings_for_user")
@patch(
    "codemie.service.aws_bedrock.bedrock_guardrail_service.BedrockGuardrailService._fetch_main_entity_names_for_setting"
)
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
    mock_fetch_main_entity_names_for_setting.return_value = ["Guardrail Test"]

    # Test first page (page=0, per_page=2)
    result = BedrockGuardrailService.get_all_settings_overview(mock_user, page=0, per_page=2)

    assert len(result["data"]) == 2
    assert result["pagination"]["total"] == 5
    assert result["pagination"]["pages"] == 3  # ceil(5/2)
    assert result["pagination"]["page"] == 0
    assert result["pagination"]["per_page"] == 2
    assert result["data"][0]["setting_id"] == "setting-0"
    assert result["data"][1]["setting_id"] == "setting-1"


@patch("codemie.service.aws_bedrock.bedrock_guardrail_service.get_all_settings_for_user")
@patch(
    "codemie.service.aws_bedrock.bedrock_guardrail_service.BedrockGuardrailService._fetch_main_entity_names_for_setting"
)
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
    result = BedrockGuardrailService.get_all_settings_overview(mock_user, page=0, per_page=10)
    assert len(result["data"]) == 1
    assert result["data"][0]["setting_id"] == "setting-1"
    assert result["data"][0]["error"] == "AWS error"
    assert result["data"][0]["invalid"] is False


@patch("codemie.service.aws_bedrock.bedrock_guardrail_service.get_all_settings_for_user")
@patch("codemie.service.aws_bedrock.bedrock_guardrail_service.get_setting_aws_credentials")
@patch("codemie.service.aws_bedrock.bedrock_guardrail_service.BedrockGuardrailService._bedrock_list_guardrails")
def test_get_all_settings_overview_limits_entity_count(
    mock_bedrock_list_guardrails,
    mock_get_setting_aws_credentials,
    mock_get_all_settings_for_user,
    mock_user,
    mock_setting,
    mock_aws_creds,
):
    """Test get_all_settings_overview limits entity count to ALL_SETTINGS_OVERVIEW_ENTITY_COUNT."""
    mock_get_all_settings_for_user.return_value = [mock_setting]
    mock_get_setting_aws_credentials.return_value = mock_aws_creds

    # Create more than 4 guardrails
    many_guardrails = []
    for i in range(10):
        many_guardrails.append({"name": f"Guardrail {i}"})

    mock_bedrock_list_guardrails.return_value = many_guardrails, None

    result = BedrockGuardrailService.get_all_settings_overview(mock_user, page=0, per_page=10)

    # Should limit to 4 guardrails
    setting_data = result["data"][0]
    assert len(setting_data["entities"]) == 4


# --- Tests for list_main_entities ---
@patch("codemie.service.aws_bedrock.bedrock_guardrail_service.get_setting_for_user")
@patch("codemie.service.aws_bedrock.bedrock_guardrail_service.get_setting_aws_credentials")
@patch("codemie.service.aws_bedrock.bedrock_guardrail_service.BedrockGuardrailService._bedrock_list_guardrails")
def test_list_main_entities_success(
    mock_bedrock_list_guardrails,
    mock_get_setting_aws_credentials,
    mock_get_setting_for_user,
    mock_user,
    mock_setting,
    mock_aws_creds,
    guardrails_response,
):
    """Test list_main_entities returns correct guardrail data."""
    mock_get_setting_for_user.return_value = mock_setting
    mock_get_setting_aws_credentials.return_value = mock_aws_creds
    mock_bedrock_list_guardrails.return_value = (guardrails_response, None)

    result, next_token = BedrockGuardrailService.list_main_entities(mock_user, "setting-1", page=0, per_page=10)

    assert len(result) == 3
    assert result[0]["id"] == "g1"
    assert result[0]["name"] == "Guardrail 1"
    assert result[0]["status"] == "PREPARED"
    assert result[1]["id"] == "g2"
    assert result[1]["name"] == "Guardrail 2"
    assert result[1]["status"] == "NOT_PREPARED"
    assert result[2]["id"] == "g3"
    assert result[2]["name"] == "Guardrail 3"
    assert result[2]["status"] == "PREPARED"
    assert next_token is None


@patch("codemie.service.aws_bedrock.bedrock_guardrail_service.get_setting_for_user")
@patch("codemie.service.aws_bedrock.bedrock_guardrail_service.get_setting_aws_credentials")
@patch("codemie.service.aws_bedrock.bedrock_guardrail_service.BedrockGuardrailService._bedrock_list_guardrails")
def test_list_main_entities_empty(
    mock_bedrock_list_guardrails,
    mock_get_setting_aws_credentials,
    mock_get_setting_for_user,
    mock_user,
    mock_setting,
    mock_aws_creds,
):
    """Test list_main_entities with no guardrails."""
    mock_get_setting_for_user.return_value = mock_setting
    mock_get_setting_aws_credentials.return_value = mock_aws_creds
    mock_bedrock_list_guardrails.return_value = ([], None)

    result, next_token = BedrockGuardrailService.list_main_entities(mock_user, "setting-1", page=0, per_page=10)

    assert result == []
    assert next_token is None


# --- Tests for get_main_entity_detail ---
@patch("codemie.service.aws_bedrock.bedrock_guardrail_service.get_setting_for_user")
@patch("codemie.service.aws_bedrock.bedrock_guardrail_service.get_setting_aws_credentials")
@patch("codemie.service.aws_bedrock.bedrock_guardrail_service.BedrockGuardrailService._bedrock_get_guardrail")
def test_get_main_entity_detail_success(
    mock_bedrock_get_guardrail,
    mock_get_setting_aws_credentials,
    mock_get_setting_for_user,
    mock_user,
    mock_setting,
    mock_aws_creds,
):
    """Test get_main_entity_detail returns correct guardrail information."""
    mock_get_setting_for_user.return_value = mock_setting
    mock_get_setting_aws_credentials.return_value = mock_aws_creds

    guardrail_detail = {
        "guardrailId": "g1",
        "guardrailArn": "arn:aws:bedrock:us-east-1:123456789012:guardrail/g1",
        "name": "Guardrail 1",
        "description": "Test guardrail description",
        "version": "DRAFT",
        "status": "READY",
        "blockedInputMessaging": "Blocked input message",
        "blockedOutputsMessaging": "Blocked output message",
        "contentPolicy": {"filters": [{"type": "SEXUAL", "inputStrength": "HIGH", "outputStrength": "HIGH"}]},
        "createdAt": "2024-01-01T00:00:00Z",
        "updatedAt": "2024-01-01T00:00:00Z",
    }
    mock_bedrock_get_guardrail.return_value = guardrail_detail

    result = BedrockGuardrailService.get_main_entity_detail(mock_user, "g1", "setting-1")

    assert result["id"] == "g1"
    assert result["name"] == "Guardrail 1"
    assert result["description"] == "Test guardrail description"
    assert result["version"] == "DRAFT"
    assert result["status"] == "PREPARED"

    mock_get_setting_for_user.assert_called_once_with(mock_user, "setting-1")
    mock_get_setting_aws_credentials.assert_called_once_with(mock_setting.id)
    mock_bedrock_get_guardrail.assert_called_once_with(
        guardrail_id="g1",
        region=mock_aws_creds.region,
        access_key_id=mock_aws_creds.access_key_id,
        secret_access_key=mock_aws_creds.secret_access_key,
        session_token=mock_aws_creds.session_token,
    )


@patch("codemie.service.aws_bedrock.bedrock_guardrail_service.get_setting_for_user")
@patch("codemie.service.aws_bedrock.bedrock_guardrail_service.get_setting_aws_credentials")
@patch("codemie.service.aws_bedrock.bedrock_guardrail_service.BedrockGuardrailService._bedrock_get_guardrail")
def test_get_main_entity_detail_not_found(
    mock_bedrock_get_guardrail,
    mock_get_setting_aws_credentials,
    mock_get_setting_for_user,
    mock_user,
    mock_setting,
    mock_aws_creds,
):
    """Test get_main_entity_detail when guardrail is not found."""
    mock_get_setting_for_user.return_value = mock_setting
    mock_get_setting_aws_credentials.return_value = mock_aws_creds
    mock_bedrock_get_guardrail.return_value = None

    with pytest.raises(ExtendedHTTPException):
        BedrockGuardrailService.get_main_entity_detail(mock_user, "g1", "setting-1")


# --- Tests for list_importable_entities_for_main_entity ---
@patch("codemie.service.aws_bedrock.bedrock_guardrail_service.get_setting_for_user")
@patch("codemie.service.aws_bedrock.bedrock_guardrail_service.get_setting_aws_credentials")
@patch("codemie.rest_api.models.guardrail.Guardrail.get_by_bedrock_aws_settings_id")
@patch("codemie.service.aws_bedrock.bedrock_guardrail_service.BedrockGuardrailService._bedrock_list_guardrails")
def test_list_importable_entities_for_main_entity_success(
    mock_bedrock_list_guardrails,
    mock_get_by_bedrock_aws_settings_id,
    mock_get_setting_aws_credentials,
    mock_get_setting_for_user,
    mock_user,
    mock_setting,
    mock_aws_creds,
):
    """Test list_importable_entities_for_main_entity returns correct guardrail versions."""
    mock_get_setting_for_user.return_value = mock_setting
    mock_get_setting_aws_credentials.return_value = mock_aws_creds
    mock_get_by_bedrock_aws_settings_id.return_value = []

    # Mock guardrail versions for a specific guardrail
    guardrail_versions = [
        {"id": "g1", "version": "1", "name": "Guardrail 1", "status": "READY", "description": "desc1"},
        {"id": "g1", "version": "2", "name": "Guardrail 1", "status": "READY", "description": "desc1 v2"},
        {"id": "g1", "version": "DRAFT", "name": "Guardrail 1", "status": "READY", "description": "draft version"},
    ]
    mock_bedrock_list_guardrails.return_value = (guardrail_versions, None)

    result, next_token = BedrockGuardrailService.list_importable_entities_for_main_entity(
        mock_user, "g1", "setting-1", page=0, per_page=10
    )

    assert len(result) == 3
    assert next_token is None

    # Check first version (should be PREPARED)
    assert result[0]["id"] == "g1"
    assert result[0]["version"] == "1"
    assert result[0]["name"] == "Guardrail 1"
    assert result[0]["status"] == "PREPARED"

    # Check second version (should be PREPARED)
    assert result[1]["id"] == "g1"
    assert result[1]["version"] == "2"
    assert result[1]["status"] == "PREPARED"

    # Check draft version (should be NOT_PREPARED due to DRAFT version)
    assert result[2]["id"] == "g1"
    assert result[2]["version"] == "DRAFT"
    assert result[2]["status"] == "NOT_PREPARED"


@patch("codemie.service.aws_bedrock.bedrock_guardrail_service.get_setting_for_user")
@patch("codemie.service.aws_bedrock.bedrock_guardrail_service.get_setting_aws_credentials")
@patch("codemie.rest_api.models.guardrail.Guardrail.get_by_bedrock_aws_settings_id")
@patch("codemie.service.aws_bedrock.bedrock_guardrail_service.BedrockGuardrailService._bedrock_list_guardrails")
def test_list_importable_entities_for_main_entity_with_existing_entities(
    mock_bedrock_list_guardrails,
    mock_get_by_bedrock_aws_settings_id,
    mock_get_setting_aws_credentials,
    mock_get_setting_for_user,
    mock_user,
    mock_setting,
    mock_aws_creds,
):
    """Test list_importable_entities_for_main_entity includes aiRunId for existing entities."""
    mock_get_setting_for_user.return_value = mock_setting
    mock_get_setting_aws_credentials.return_value = mock_aws_creds

    # Mock existing entity
    existing_guardrail = MagicMock()
    existing_guardrail.id = "guardrail-123"
    existing_guardrail.bedrock.bedrock_guardrail_id = "g1"
    existing_guardrail.bedrock.bedrock_version = "1"
    mock_get_by_bedrock_aws_settings_id.return_value = [existing_guardrail]

    guardrail_versions = [
        {"id": "g1", "version": "1", "name": "Guardrail 1", "status": "READY", "description": "desc1"},
    ]
    mock_bedrock_list_guardrails.return_value = (guardrail_versions, None)

    result, next_token = BedrockGuardrailService.list_importable_entities_for_main_entity(
        mock_user, "g1", "setting-1", page=0, per_page=10
    )

    assert len(result) == 1
    assert result[0]["id"] == "g1"
    assert result[0]["version"] == "1"
    assert result[0]["aiRunId"] == "guardrail-123"
    assert next_token is None


@patch("codemie.service.aws_bedrock.bedrock_guardrail_service.get_setting_for_user")
@patch("codemie.service.aws_bedrock.bedrock_guardrail_service.get_setting_aws_credentials")
@patch("codemie.rest_api.models.guardrail.Guardrail.get_by_bedrock_aws_settings_id")
@patch("codemie.service.aws_bedrock.bedrock_guardrail_service.BedrockGuardrailService._bedrock_list_guardrails")
def test_list_importable_entities_for_main_entity_handles_exceptions(
    mock_bedrock_list_guardrails,
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
    mock_bedrock_list_guardrails.side_effect = Exception("AWS error")

    with pytest.raises(ExtendedHTTPException):
        BedrockGuardrailService.list_importable_entities_for_main_entity(
            mock_user, "g1", "setting-1", page=0, per_page=10
        )


# --- Tests for get_importable_entity_detail ---
@patch("codemie.service.aws_bedrock.bedrock_guardrail_service.get_setting_for_user")
@patch("codemie.service.aws_bedrock.bedrock_guardrail_service.get_setting_aws_credentials")
@patch("codemie.service.aws_bedrock.bedrock_guardrail_service.BedrockGuardrailService._bedrock_get_guardrail")
def test_get_importable_entity_detail_success(
    mock_get_guardrail,
    mock_get_setting_aws_credentials,
    mock_get_setting_for_user,
    mock_user,
    mock_setting,
    mock_aws_creds,
):
    """Test get_importable_entity_detail returns correct guardrail information."""
    mock_get_setting_for_user.return_value = mock_setting
    mock_get_setting_aws_credentials.return_value = mock_aws_creds

    guardrail_detail = {
        "guardrailId": "g1",
        "guardrailArn": "arn:aws:bedrock:us-east-1:123456789012:guardrail/g1",
        "name": "Guardrail 1",
        "description": "Test guardrail description",
        "version": "1",
        "status": "READY",
        "topicPolicy": {"topics": []},
        "contentPolicy": {"filters": []},
        "wordPolicy": {"words": []},
        "sensitiveInformationPolicy": {"piiEntities": []},
        "createdAt": "2024-01-01T00:00:00Z",
        "updatedAt": "2024-01-01T00:00:00Z",
    }
    mock_get_guardrail.return_value = guardrail_detail

    result = BedrockGuardrailService.get_importable_entity_detail(mock_user, "g1", "1", "setting-1")

    assert result["id"] == "g1"
    assert result["name"] == "Guardrail 1"
    assert result["version"] == "1"
    assert result["description"] == "Test guardrail description"
    assert result["status"] == "PREPARED"


@patch("codemie.service.aws_bedrock.bedrock_guardrail_service.get_setting_for_user")
@patch("codemie.service.aws_bedrock.bedrock_guardrail_service.get_setting_aws_credentials")
@patch("codemie.service.aws_bedrock.bedrock_guardrail_service.BedrockGuardrailService._bedrock_get_guardrail")
def test_get_importable_entity_detail_not_found(
    mock_get_guardrail,
    mock_get_setting_aws_credentials,
    mock_get_setting_for_user,
    mock_user,
    mock_setting,
    mock_aws_creds,
):
    """Test get_importable_entity_detail when guardrail info is not found."""
    mock_get_setting_for_user.return_value = mock_setting
    mock_get_setting_aws_credentials.return_value = mock_aws_creds
    mock_get_guardrail.return_value = None

    result = BedrockGuardrailService.get_importable_entity_detail(mock_user, "g1", "1", "setting-1")

    assert result == {}


# --- Tests for import_entities ---
@patch("codemie.service.aws_bedrock.bedrock_guardrail_service.get_setting_for_user")
@patch("codemie.service.aws_bedrock.bedrock_guardrail_service.get_setting_aws_credentials")
@patch("codemie.rest_api.models.guardrail.Guardrail.get_by_bedrock_aws_settings_id")
@patch("codemie.service.aws_bedrock.bedrock_guardrail_service.BedrockGuardrailService._process_entity_import")
def test_import_entities_success(
    mock_process_entity_import,
    mock_get_by_bedrock_aws_settings_id,
    mock_get_setting_aws_credentials,
    mock_get_setting_for_user,
    mock_user,
    mock_setting,
    mock_aws_creds,
    import_guardrail,
):
    """Test import_entities calls _process_entity_import correctly."""
    mock_get_setting_for_user.return_value = mock_setting
    mock_get_setting_aws_credentials.return_value = mock_aws_creds
    mock_get_by_bedrock_aws_settings_id.return_value = []
    mock_process_entity_import.return_value = {"guardrailId": "g1", "version": "1", "aiRunId": "run-123"}

    import_payload = {"setting-1": [import_guardrail]}
    result = BedrockGuardrailService.import_entities(mock_user, import_payload)

    assert len(result) == 1
    assert result[0]["guardrailId"] == "g1"
    assert result[0]["version"] == "1"
    assert result[0]["aiRunId"] == "run-123"
    mock_process_entity_import.assert_called_once()


# --- Tests for delete_entities ---
@patch("codemie.rest_api.models.guardrail.Guardrail.get_by_bedrock_aws_settings_id")
@patch("codemie.service.guardrail.guardrail_service.GuardrailService.remove_guardrail_assignments_for_guardrail")
def test_delete_entities_deletes_all(mock_remove_guardrails, mock_get_by_bedrock_aws_settings_id, mock_guardrail_obj):
    """Test delete_entities deletes all guardrails for a given setting_id."""
    mock_remove_guardrails.return_value = None

    mock_get_by_bedrock_aws_settings_id.return_value = [mock_guardrail_obj]
    BedrockGuardrailService.delete_entities("setting-1")
    mock_guardrail_obj.delete.assert_called_once()


# --- Tests for apply_guardrail ---
@patch("codemie.rest_api.models.settings.Settings.get_by_id")
@patch("codemie.service.aws_bedrock.bedrock_guardrail_service.get_setting_aws_credentials")
def test_apply_guardrail_success(
    mock_get_setting_aws_creds,
    mock_settings_get_by_id,
    mock_guardrail_obj,
    mock_aws_creds,
):
    """Test apply_guardrail successfully applies a guardrail."""
    mock_settings_get_by_id.return_value = MagicMock(id="setting-1")
    mock_get_setting_aws_creds.return_value = mock_aws_creds
    with patch.object(BedrockGuardrailService, "_bedrock_apply_guardrail", return_value={"result": "ok"}) as mock_apply:
        result = BedrockGuardrailService.apply_guardrail(
            guardrail=mock_guardrail_obj,
            content=[{"text": {"text": "abc", "qualifiers": []}}],
            source="INPUT",
            output_scope="FULL",
        )
        assert result == {"result": "ok"}
        mock_apply.assert_called_once()


@patch("codemie.rest_api.models.settings.Settings.get_by_id")
@patch("codemie.service.aws_bedrock.bedrock_guardrail_service.get_setting_aws_credentials")
def test_apply_guardrail_missing_setting(
    mock_get_setting_aws_creds,
    mock_settings_get_by_id,
    mock_guardrail_obj,
):
    """Test apply_guardrail raises error when setting is not found."""
    mock_settings_get_by_id.return_value = None
    with pytest.raises(ValueError):
        BedrockGuardrailService.apply_guardrail(
            guardrail=mock_guardrail_obj,
            content=[{"text": {"text": "abc", "qualifiers": []}}],
            source="INPUT",
            output_scope="FULL",
        )


@patch("codemie.rest_api.models.settings.Settings.get_by_id")
@patch("codemie.service.aws_bedrock.bedrock_guardrail_service.get_setting_aws_credentials")
def test_apply_guardrail_missing_creds(
    mock_get_setting_aws_creds,
    mock_settings_get_by_id,
    mock_guardrail_obj,
):
    """Test apply_guardrail raises error when AWS credentials are not found."""
    mock_settings_get_by_id.return_value = MagicMock(id="setting-1")
    mock_get_setting_aws_creds.return_value = None
    with pytest.raises(ValueError):
        BedrockGuardrailService.apply_guardrail(
            guardrail=mock_guardrail_obj,
            content=[{"text": {"text": "abc", "qualifiers": []}}],
            source="INPUT",
            output_scope="FULL",
        )


@patch("codemie.rest_api.models.settings.Settings.get_by_id")
@patch("codemie.service.aws_bedrock.bedrock_guardrail_service.get_setting_aws_credentials")
def test_apply_guardrail_missing_bedrock(
    mock_get_setting_aws_creds,
    mock_settings_get_by_id,
    mock_guardrail_obj,
    mock_aws_creds,
):
    """Test apply_guardrail raises error when guardrail has no bedrock information."""
    mock_settings_get_by_id.return_value = MagicMock(id="setting-1")
    mock_get_setting_aws_creds.return_value = mock_aws_creds
    mock_guardrail_obj.bedrock = None
    with pytest.raises(ValueError):
        BedrockGuardrailService.apply_guardrail(
            guardrail=mock_guardrail_obj,
            content=[{"text": {"text": "abc", "qualifiers": []}}],
            source="INPUT",
            output_scope="FULL",
        )


# --- Tests for validate_remote_entity_exists_and_cleanup ---
@patch("codemie.rest_api.models.settings.Settings.get_by_id")
@patch("codemie.service.aws_bedrock.bedrock_guardrail_service.get_setting_aws_credentials")
@patch("codemie.service.aws_bedrock.bedrock_guardrail_service.BedrockGuardrailService._bedrock_get_guardrail")
def test_validate_remote_entity_exists_and_cleanup_success(
    mock_bedrock_get_guardrail,
    mock_get_setting_aws_credentials,
    mock_settings_get_by_id,
    mock_aws_creds,
):
    """Test validate_remote_entity_exists_and_cleanup when guardrail exists remotely."""
    # Create mock guardrail with Bedrock configuration
    mock_guardrail = MagicMock()
    mock_guardrail.bedrock.bedrock_guardrail_id = "gr-123"
    mock_guardrail.bedrock.bedrock_version = "1"
    mock_guardrail.bedrock.bedrock_aws_settings_id = "setting-789"
    mock_guardrail.name = "Test Guardrail"

    mock_setting = MagicMock()
    mock_settings_get_by_id.return_value = mock_setting
    mock_get_setting_aws_credentials.return_value = mock_aws_creds
    mock_bedrock_get_guardrail.return_value = {"guardrailId": "gr-123"}

    result = BedrockGuardrailService.validate_remote_entity_exists_and_cleanup(mock_guardrail)

    assert result is None
    mock_settings_get_by_id.assert_called_once_with("setting-789")
    mock_get_setting_aws_credentials.assert_called_once_with(mock_setting.id)
    mock_bedrock_get_guardrail.assert_called_once_with(
        guardrail_id="gr-123",
        guardrail_version="1",
        region=mock_aws_creds.region,
        access_key_id=mock_aws_creds.access_key_id,
        secret_access_key=mock_aws_creds.secret_access_key,
        session_token=mock_aws_creds.session_token,
    )
    mock_guardrail.delete.assert_not_called()


@patch("codemie.rest_api.models.settings.Settings.get_by_id")
@patch("codemie.service.aws_bedrock.bedrock_guardrail_service.get_setting_aws_credentials")
@patch("codemie.service.aws_bedrock.bedrock_guardrail_service.BedrockGuardrailService._bedrock_get_guardrail")
@patch("codemie.service.guardrail.guardrail_service.GuardrailService.remove_guardrail_assignments_for_guardrail")
def test_validate_remote_entity_exists_and_cleanup_resource_not_found(
    mock_remove_guardrails,
    mock_bedrock_get_guardrail,
    mock_get_setting_aws_credentials,
    mock_settings_get_by_id,
    mock_aws_creds,
):
    """Test validate_remote_entity_exists_and_cleanup when guardrail is deleted remotely."""
    from botocore.exceptions import ClientError

    mock_remove_guardrails.return_value = None

    # Create mock guardrail with Bedrock configuration
    mock_guardrail = MagicMock()
    mock_guardrail.bedrock.bedrock_guardrail_id = "gr-123"
    mock_guardrail.bedrock.bedrock_version = "1"
    mock_guardrail.bedrock.bedrock_aws_settings_id = "setting-789"
    mock_guardrail.name = "Test Guardrail"

    mock_setting = MagicMock()
    mock_settings_get_by_id.return_value = mock_setting
    mock_get_setting_aws_credentials.return_value = mock_aws_creds

    # Mock ResourceNotFoundException
    error_response = {"Error": {"Code": "ResourceNotFoundException", "Message": "Guardrail not found"}}
    mock_bedrock_get_guardrail.side_effect = ClientError(error_response, "GetGuardrail")

    BedrockGuardrailService.validate_remote_entity_exists_and_cleanup(mock_guardrail)

    mock_guardrail.delete.assert_called_once()


@patch("codemie.rest_api.models.settings.Settings.get_by_id")
@patch("codemie.service.aws_bedrock.bedrock_guardrail_service.get_setting_aws_credentials")
@patch("codemie.service.aws_bedrock.bedrock_guardrail_service.BedrockGuardrailService._bedrock_get_guardrail")
def test_validate_remote_entity_exists_and_cleanup_other_client_error_passes(
    mock_bedrock_get_guardrail,
    mock_get_setting_aws_credentials,
    mock_settings_get_by_id,
    mock_aws_creds,
):
    """Test validate_remote_entity_exists_and_cleanup with other AWS client errors."""
    from botocore.exceptions import ClientError

    # Create mock guardrail with Bedrock configuration
    mock_guardrail = MagicMock()
    mock_guardrail.bedrock.bedrock_guardrail_id = "gr-123"
    mock_guardrail.bedrock.bedrock_version = "1"
    mock_guardrail.bedrock.bedrock_aws_settings_id = "setting-789"
    mock_guardrail.name = "Test Guardrail"

    mock_setting = MagicMock()
    mock_settings_get_by_id.return_value = mock_setting
    mock_get_setting_aws_credentials.return_value = mock_aws_creds

    # Mock other client error
    error_response = {"Error": {"Code": "AccessDeniedException", "Message": "Access denied"}}
    mock_bedrock_get_guardrail.side_effect = ClientError(error_response, "GetGuardrail")

    BedrockGuardrailService.validate_remote_entity_exists_and_cleanup(mock_guardrail)

    mock_guardrail.delete.assert_not_called()


def test_validate_remote_entity_exists_and_cleanup_non_bedrock_guardrail():
    """Test validate_remote_entity_exists_and_cleanup with non-Bedrock guardrail."""
    # Create mock guardrail without Bedrock configuration
    mock_guardrail = MagicMock()
    mock_guardrail.bedrock = None

    result = BedrockGuardrailService.validate_remote_entity_exists_and_cleanup(mock_guardrail)

    assert result is None
    mock_guardrail.delete.assert_not_called()


def test_validate_remote_entity_exists_and_cleanup_missing_bedrock_fields():
    """Test validate_remote_entity_exists_and_cleanup with incomplete Bedrock configuration."""
    # Create mock guardrail with incomplete Bedrock configuration
    mock_guardrail = MagicMock()
    mock_guardrail.bedrock.bedrock_guardrail_id = None
    mock_guardrail.bedrock.bedrock_version = "1"

    result = BedrockGuardrailService.validate_remote_entity_exists_and_cleanup(mock_guardrail)

    assert result is None
    mock_guardrail.delete.assert_not_called()


@patch("codemie.rest_api.models.settings.Settings.get_by_id")
def test_validate_remote_entity_exists_and_cleanup_setting_not_found_passes(
    mock_settings_get_by_id,
):
    """Test validate_remote_entity_exists_and_cleanup when setting is not found."""
    # Create mock guardrail with Bedrock configuration
    mock_guardrail = MagicMock()
    mock_guardrail.bedrock.bedrock_guardrail_id = "gr-123"
    mock_guardrail.bedrock.bedrock_version = "1"
    mock_guardrail.bedrock.bedrock_aws_settings_id = "setting-789"

    mock_settings_get_by_id.return_value = None

    BedrockGuardrailService.validate_remote_entity_exists_and_cleanup(mock_guardrail)

    mock_guardrail.delete.assert_not_called()


@patch("codemie.rest_api.models.settings.Settings.get_by_id")
def test_validate_remote_entity_exists_and_cleanup_unexpected_error_passes(
    mock_settings_get_by_id,
):
    """Test validate_remote_entity_exists_and_cleanup with unexpected errors."""
    # Create mock guardrail with Bedrock configuration
    mock_guardrail = MagicMock()
    mock_guardrail.bedrock.bedrock_guardrail_id = "gr-123"
    mock_guardrail.bedrock.bedrock_version = "1"
    mock_guardrail.bedrock.bedrock_aws_settings_id = "setting-789"
    mock_guardrail.name = "Test Guardrail"

    mock_settings_get_by_id.side_effect = Exception("Unexpected error")

    BedrockGuardrailService.validate_remote_entity_exists_and_cleanup(mock_guardrail)

    mock_guardrail.delete.assert_not_called()
