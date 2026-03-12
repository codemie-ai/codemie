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
from codemie.rest_api.models.vendor import ImportKnowledgeBase
from codemie.service.aws_bedrock.bedrock_knowledge_base_service import BedrockKnowledgeBaseService
from codemie.rest_api.models.settings import AWSCredentials, Settings
from codemie.rest_api.security.user import User
from codemie.rest_api.models.index import IndexInfo


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
def kb_import():
    return ImportKnowledgeBase(id="kb-1", setting_id="setting-1")


@pytest.fixture
def kb_detail_vector():
    return {
        "knowledgeBaseId": "kb-1",
        "name": "KB 1",
        "description": "desc",
        "status": "ACTIVE",
        "knowledgeBaseArn": "arn:aws:bedrock:us-east-1:123456789012:knowledge-base/kb-1",
        "roleArn": "arn:aws:iam::123456789012:role/BedrockRole",
        "knowledgeBaseConfiguration": {
            "type": "VECTOR",
            "vectorKnowledgeBaseConfiguration": {
                "embeddingModelArn": "arn:aws:bedrock:us-east-1::foundation-model/amazon.titan-embed-text-v1"
            },
        },
        "storageConfiguration": {"type": "OPENSEARCH_SERVERLESS"},
        "createdAt": "2024-01-01T00:00:00Z",
        "updatedAt": "2024-01-02T00:00:00Z",
        "failureReasons": [],
    }


@pytest.fixture
def kb_detail_kendra():
    return {
        "knowledgeBaseId": "kb-kendra-1",
        "name": "KB Kendra 1",
        "description": "kendra desc",
        "status": "ACTIVE",
        "knowledgeBaseArn": "arn:aws:bedrock:us-east-1:123456789012:knowledge-base/kb-kendra-1",
        "roleArn": "arn:aws:iam::123456789012:role/BedrockRole",
        "knowledgeBaseConfiguration": {
            "type": "KENDRA",
            "kendraKnowledgeBaseConfiguration": {
                "kendraIndexArn": "arn:aws:kendra:us-east-1:123456789012:index/kendra-index-123"
            },
        },
        "storageConfiguration": {"type": "KENDRA"},
        "createdAt": "2024-03-01T00:00:00Z",
        "updatedAt": "2024-03-02T00:00:00Z",
        "failureReasons": [],
    }


@pytest.fixture
def kb_index():
    index = MagicMock(spec=IndexInfo)
    index.id = "index-1"
    index.setting_id = "setting-1"

    bedrock = MagicMock()
    bedrock.bedrock_knowledge_base_id = "kb-1"
    index.bedrock = bedrock

    index.created_by = MagicMock(id="user-id", username="testuser", name="Test User")
    return index


# --- Tests for get_all_settings_overview ---
@patch("codemie.service.aws_bedrock.bedrock_knowledge_base_service.get_all_settings_for_user")
@patch(
    "codemie.service.aws_bedrock.bedrock_knowledge_base_service.BedrockKnowledgeBaseService._fetch_main_entity_names_for_setting"
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

    # Mock the fetch method to return knowledge base names for different settings
    mock_fetch_main_entity_names_for_setting.side_effect = [
        ["KB 1"],  # For setting-1
        ["KB 2"],  # For setting-2
    ]

    result = BedrockKnowledgeBaseService.get_all_settings_overview(mock_user, page=0, per_page=10)

    # Assertions
    assert "data" in result
    assert "pagination" in result
    assert len(result["data"]) == 2

    # Check first setting
    setting1_data = result["data"][0]
    assert setting1_data["setting_id"] == "setting-1"
    assert setting1_data["setting_name"] == "Setting 1"
    assert setting1_data["project"] == "project-1"
    assert setting1_data["entities"] == ["KB 1"]

    # Check second setting
    setting2_data = result["data"][1]
    assert setting2_data["setting_id"] == "setting-2"
    assert setting2_data["setting_name"] == "Setting 2"
    assert setting2_data["project"] == "project-2"
    assert setting2_data["entities"] == ["KB 2"]


@patch("codemie.service.aws_bedrock.bedrock_knowledge_base_service.get_all_settings_for_user")
@patch(
    "codemie.service.aws_bedrock.bedrock_knowledge_base_service.BedrockKnowledgeBaseService._fetch_main_entity_names_for_setting"
)
def test_get_all_settings_overview_empty_settings(
    mock_fetch_main_entity_names_for_setting,
    mock_get_all_settings_for_user,
    mock_user,
):
    """Test get_all_settings_overview with no settings available."""
    mock_get_all_settings_for_user.return_value = []

    result = BedrockKnowledgeBaseService.get_all_settings_overview(mock_user, page=0, per_page=10)

    assert result["data"] == []
    assert result["pagination"]["total"] == 0
    assert result["pagination"]["pages"] == 0
    assert result["pagination"]["page"] == 0
    assert result["pagination"]["per_page"] == 10

    # Should not call the fetch method when there are no settings
    mock_fetch_main_entity_names_for_setting.assert_not_called()


@patch("codemie.service.aws_bedrock.bedrock_knowledge_base_service.get_all_settings_for_user")
@patch(
    "codemie.service.aws_bedrock.bedrock_knowledge_base_service.BedrockKnowledgeBaseService._fetch_main_entity_names_for_setting"
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
    mock_fetch_main_entity_names_for_setting.return_value = ["KB Test"]

    # Test first page (page=0, per_page=2)
    result = BedrockKnowledgeBaseService.get_all_settings_overview(mock_user, page=0, per_page=2)

    assert len(result["data"]) == 2
    assert result["pagination"]["total"] == 5
    assert result["pagination"]["pages"] == 3  # ceil(5/2)
    assert result["pagination"]["page"] == 0
    assert result["pagination"]["per_page"] == 2
    assert result["data"][0]["setting_id"] == "setting-0"
    assert result["data"][1]["setting_id"] == "setting-1"


@patch("codemie.service.aws_bedrock.bedrock_knowledge_base_service.get_all_settings_for_user")
@patch(
    "codemie.service.aws_bedrock.bedrock_knowledge_base_service.BedrockKnowledgeBaseService._fetch_main_entity_names_for_setting"
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
    result = BedrockKnowledgeBaseService.get_all_settings_overview(mock_user, page=0, per_page=10)
    assert len(result["data"]) == 1
    assert result["data"][0]["setting_id"] == "setting-1"
    assert result["data"][0]["error"] == "AWS error"
    assert result["data"][0]["invalid"] is False


@patch("codemie.service.aws_bedrock.bedrock_knowledge_base_service.get_all_settings_for_user")
@patch("codemie.service.aws_bedrock.bedrock_knowledge_base_service.get_setting_aws_credentials")
@patch(
    "codemie.service.aws_bedrock.bedrock_knowledge_base_service.BedrockKnowledgeBaseService._bedrock_list_knowledge_bases"
)
def test_get_all_settings_overview_limits_entity_count(
    mock_bedrock_list_knowledge_bases,
    mock_get_setting_aws_credentials,
    mock_get_all_settings_for_user,
    mock_user,
    mock_setting,
    mock_aws_creds,
):
    """Test get_all_settings_overview limits entity count to ALL_SETTINGS_OVERVIEW_ENTITY_COUNT."""
    mock_get_all_settings_for_user.return_value = [mock_setting]
    mock_get_setting_aws_credentials.return_value = mock_aws_creds

    # Create more than 4 knowledge bases
    many_kbs = []
    for i in range(10):
        many_kbs.append({"name": f"KB {i}"})

    mock_bedrock_list_knowledge_bases.return_value = many_kbs, None

    result = BedrockKnowledgeBaseService.get_all_settings_overview(mock_user, page=0, per_page=10)

    # Should limit to 4 knowledge bases
    setting_data = result["data"][0]
    assert len(setting_data["entities"]) == 4


# --- Tests for list_main_entities ---
@patch("codemie.service.aws_bedrock.bedrock_knowledge_base_service.get_setting_for_user")
@patch("codemie.service.aws_bedrock.bedrock_knowledge_base_service.get_setting_aws_credentials")
@patch("codemie.rest_api.models.index.IndexInfo.get_by_bedrock_aws_settings_id")
@patch(
    "codemie.service.aws_bedrock.bedrock_knowledge_base_service.BedrockKnowledgeBaseService._bedrock_list_knowledge_bases"
)
def test_list_main_entities_success(
    mock_bedrock_list_knowledge_bases,
    mock_get_by_bedrock_aws_settings_id,
    mock_get_setting_aws_credentials,
    mock_get_setting_for_user,
    mock_user,
    mock_setting,
    mock_aws_creds,
):
    """Test list_main_entities returns correct knowledge base data."""
    mock_get_setting_for_user.return_value = mock_setting
    mock_get_setting_aws_credentials.return_value = mock_aws_creds
    mock_get_by_bedrock_aws_settings_id.return_value = []

    kb_data = [
        {
            "knowledgeBaseId": "kb-1",
            "name": "KB 1",
            "status": "ACTIVE",
            "description": "Description 1",
            "updatedAt": "2024-01-01T00:00:00Z",
        },
        {
            "knowledgeBaseId": "kb-2",
            "name": "KB 2",
            "status": "CREATING",
            "description": "Description 2",
            "updatedAt": "2024-01-02T00:00:00Z",
        },
    ]
    mock_bedrock_list_knowledge_bases.return_value = (kb_data, None)

    result, next_token = BedrockKnowledgeBaseService.list_main_entities(mock_user, "setting-1", page=0, per_page=10)

    assert len(result) == 2
    assert result[0]["id"] == "kb-1"
    assert result[0]["name"] == "KB 1"
    assert result[0]["status"] == "PREPARED"
    assert result[1]["id"] == "kb-2"
    assert result[1]["name"] == "KB 2"
    assert result[1]["status"] == "NOT_PREPARED"
    assert next_token is None


@patch("codemie.service.aws_bedrock.bedrock_knowledge_base_service.get_setting_for_user")
@patch("codemie.service.aws_bedrock.bedrock_knowledge_base_service.get_setting_aws_credentials")
@patch("codemie.rest_api.models.index.IndexInfo.get_by_bedrock_aws_settings_id")
@patch(
    "codemie.service.aws_bedrock.bedrock_knowledge_base_service.BedrockKnowledgeBaseService._bedrock_list_knowledge_bases"
)
def test_list_main_entities_empty(
    mock_bedrock_list_knowledge_bases,
    mock_get_by_bedrock_aws_settings_id,
    mock_get_setting_aws_credentials,
    mock_get_setting_for_user,
    mock_user,
    mock_setting,
    mock_aws_creds,
):
    """Test list_main_entities with no knowledge bases."""
    mock_get_setting_for_user.return_value = mock_setting
    mock_get_setting_aws_credentials.return_value = mock_aws_creds
    mock_get_by_bedrock_aws_settings_id.return_value = []
    mock_bedrock_list_knowledge_bases.return_value = ([], None)

    result, next_token = BedrockKnowledgeBaseService.list_main_entities(mock_user, "setting-1", page=0, per_page=10)

    assert result == []
    assert next_token is None


# --- Tests for get_main_entity_detail ---
@patch("codemie.service.aws_bedrock.bedrock_knowledge_base_service.get_setting_for_user")
@patch("codemie.service.aws_bedrock.bedrock_knowledge_base_service.get_setting_aws_credentials")
@patch("codemie.rest_api.models.index.IndexInfo.get_by_bedrock_aws_settings_id")
@patch(
    "codemie.service.aws_bedrock.bedrock_knowledge_base_service.BedrockKnowledgeBaseService._bedrock_get_knowledge_base"
)
def test_get_main_entity_detail_success(
    mock_bedrock_get_knowledge_base,
    mock_get_by_bedrock_aws_settings_id,
    mock_get_setting_aws_credentials,
    mock_get_setting_for_user,
    mock_user,
    mock_setting,
    mock_aws_creds,
):
    """Test get_main_entity_detail returns correct knowledge base information."""
    mock_get_by_bedrock_aws_settings_id.return_value = []
    mock_get_setting_for_user.return_value = mock_setting
    mock_get_setting_aws_credentials.return_value = mock_aws_creds

    kb_detail_vector = {
        "knowledgeBaseId": "kb-1",
        "name": "KB 1",
        "description": "Test knowledge base description",
        "status": "ACTIVE",
        "knowledgeBaseArn": "arn:aws:bedrock:us-east-1:123456789012:knowledge-base/kb-1",
        "roleArn": "arn:aws:iam::123456789012:role/BedrockRole",
        "knowledgeBaseConfiguration": {
            "type": "VECTOR",
            "vectorKnowledgeBaseConfiguration": {
                "embeddingModelArn": "arn:aws:bedrock:us-east-1::foundation-model/amazon.titan-embed-text-v1"
            },
        },
        "createdAt": "2024-01-01T00:00:00Z",
        "updatedAt": "2024-01-02T00:00:00Z",
    }
    mock_bedrock_get_knowledge_base.return_value = kb_detail_vector

    result = BedrockKnowledgeBaseService.get_main_entity_detail(mock_user, "kb-1", "setting-1")

    assert result["id"] == "kb-1"
    assert result["name"] == "KB 1"
    assert result["description"] == "Test knowledge base description"
    assert result["status"] == "PREPARED"
    assert result["type"] == "VECTOR"

    mock_get_setting_for_user.assert_called_once_with(mock_user, "setting-1")
    mock_get_setting_aws_credentials.assert_called_once_with(mock_setting.id)
    mock_bedrock_get_knowledge_base.assert_called_once_with(
        knowledge_base_id="kb-1",
        region=mock_aws_creds.region,
        access_key_id=mock_aws_creds.access_key_id,
        secret_access_key=mock_aws_creds.secret_access_key,
        session_token=mock_aws_creds.session_token,
    )


@patch("codemie.service.aws_bedrock.bedrock_knowledge_base_service.get_setting_for_user")
@patch("codemie.service.aws_bedrock.bedrock_knowledge_base_service.get_setting_aws_credentials")
@patch("codemie.rest_api.models.index.IndexInfo.get_by_bedrock_aws_settings_id")
@patch(
    "codemie.service.aws_bedrock.bedrock_knowledge_base_service.BedrockKnowledgeBaseService._bedrock_get_knowledge_base"
)
def test_get_main_entity_detail_kendra(
    mock_bedrock_get_knowledge_base,
    mock_get_by_bedrock_aws_settings_id,
    mock_get_setting_aws_credentials,
    mock_get_setting_for_user,
    mock_user,
    mock_setting,
    mock_aws_creds,
    kb_detail_kendra,
):
    """Test get_main_entity_detail for KENDRA type (embeddingModel None, kendraIndexArn present)."""
    mock_get_by_bedrock_aws_settings_id.return_value = []
    mock_get_setting_for_user.return_value = mock_setting
    mock_get_setting_aws_credentials.return_value = mock_aws_creds
    mock_bedrock_get_knowledge_base.return_value = kb_detail_kendra

    result = BedrockKnowledgeBaseService.get_main_entity_detail(mock_user, "kb-kendra-1", "setting-1")

    assert result["id"] == "kb-kendra-1"
    assert result["type"] == "KENDRA"
    assert result["status"] == "PREPARED"
    assert result["embeddingModel"] is None
    assert result["kendraIndexArn"] == "arn:aws:kendra:us-east-1:123456789012:index/kendra-index-123"


@patch("codemie.service.aws_bedrock.bedrock_knowledge_base_service.get_setting_for_user")
@patch("codemie.service.aws_bedrock.bedrock_knowledge_base_service.get_setting_aws_credentials")
@patch(
    "codemie.service.aws_bedrock.bedrock_knowledge_base_service.BedrockKnowledgeBaseService._bedrock_get_knowledge_base"
)
def test_get_main_entity_detail_not_found(
    mock_bedrock_get_knowledge_base,
    mock_get_setting_aws_credentials,
    mock_get_setting_for_user,
    mock_user,
    mock_setting,
    mock_aws_creds,
):
    """Test get_main_entity_detail when knowledge base is not found."""
    mock_get_setting_for_user.return_value = mock_setting
    mock_get_setting_aws_credentials.return_value = mock_aws_creds
    mock_bedrock_get_knowledge_base.return_value = None

    with pytest.raises(ExtendedHTTPException):
        BedrockKnowledgeBaseService.get_main_entity_detail(mock_user, "kb-1", "setting-1")


# --- Tests for list_importable_entities_for_main_entity ---
@patch("codemie.service.aws_bedrock.bedrock_knowledge_base_service.get_setting_for_user")
@patch("codemie.service.aws_bedrock.bedrock_knowledge_base_service.get_setting_aws_credentials")
@patch("codemie.rest_api.models.index.IndexInfo.get_by_bedrock_aws_settings_id")
@patch(
    "codemie.service.aws_bedrock.bedrock_knowledge_base_service.BedrockKnowledgeBaseService._bedrock_get_knowledge_base"
)
def test_list_importable_entities_for_main_entity_success(
    mock_bedrock_get_knowledge_base,
    mock_get_by_bedrock_aws_settings_id,
    mock_get_setting_aws_credentials,
    mock_get_setting_for_user,
    mock_user,
    mock_setting,
    mock_aws_creds,
    kb_detail_vector,
):
    """Test list_importable_entities_for_main_entity returns correct knowledge base data."""
    mock_get_setting_for_user.return_value = mock_setting
    mock_get_setting_aws_credentials.return_value = mock_aws_creds
    mock_get_by_bedrock_aws_settings_id.return_value = []
    mock_bedrock_get_knowledge_base.return_value = kb_detail_vector

    result, next_token = BedrockKnowledgeBaseService.list_importable_entities_for_main_entity(
        mock_user, "kb-1", "setting-1", page=0, per_page=10
    )

    assert len(result) == 1
    assert result[0]["id"] == "kb-1"
    assert result[0]["type"] == "VECTOR"
    assert result[0]["embeddingModel"] == "amazon.titan-embed-text-v1"
    assert result[0]["createdAt"] == "2024-01-01T00:00:00Z"
    assert result[0]["updatedAt"] == "2024-01-02T00:00:00Z"
    assert next_token is None


@patch("codemie.service.aws_bedrock.bedrock_knowledge_base_service.get_setting_for_user")
@patch("codemie.service.aws_bedrock.bedrock_knowledge_base_service.get_setting_aws_credentials")
@patch("codemie.rest_api.models.index.IndexInfo.get_by_bedrock_aws_settings_id")
@patch(
    "codemie.service.aws_bedrock.bedrock_knowledge_base_service.BedrockKnowledgeBaseService._bedrock_get_knowledge_base"
)
def test_list_importable_entities_for_main_entity_kendra(
    mock_bedrock_get_knowledge_base,
    mock_get_by_bedrock_aws_settings_id,
    mock_get_setting_aws_credentials,
    mock_get_setting_for_user,
    mock_user,
    mock_setting,
    mock_aws_creds,
    kb_detail_kendra,
):
    """Test list_importable_entities_for_main_entity supports KENDRA type."""
    mock_get_setting_for_user.return_value = mock_setting
    mock_get_setting_aws_credentials.return_value = mock_aws_creds
    mock_get_by_bedrock_aws_settings_id.return_value = []
    mock_bedrock_get_knowledge_base.return_value = kb_detail_kendra

    result, next_token = BedrockKnowledgeBaseService.list_importable_entities_for_main_entity(
        mock_user, "kb-kendra-1", "setting-1", page=0, per_page=10
    )

    assert len(result) == 1
    entity = result[0]
    assert entity["id"] == "kb-kendra-1"
    assert entity["type"] == "KENDRA"
    assert entity["embeddingModel"] is None
    assert "kendraIndexArn" in entity
    assert next_token is None


@patch("codemie.service.aws_bedrock.bedrock_knowledge_base_service.get_setting_for_user")
@patch("codemie.service.aws_bedrock.bedrock_knowledge_base_service.get_setting_aws_credentials")
@patch("codemie.rest_api.models.index.IndexInfo.get_by_bedrock_aws_settings_id")
@patch(
    "codemie.service.aws_bedrock.bedrock_knowledge_base_service.BedrockKnowledgeBaseService._bedrock_get_knowledge_base"
)
def test_list_importable_entities_for_main_entity_with_existing_entity(
    mock_bedrock_get_knowledge_base,
    mock_get_by_bedrock_aws_settings_id,
    mock_get_setting_aws_credentials,
    mock_get_setting_for_user,
    mock_user,
    mock_setting,
    mock_aws_creds,
    kb_detail_vector,
):
    """Test list_importable_entities_for_main_entity includes aiRunId for existing entities."""
    mock_get_setting_for_user.return_value = mock_setting
    mock_get_setting_aws_credentials.return_value = mock_aws_creds

    # Mock existing entity
    existing_index = MagicMock()
    existing_index.id = "index-123"
    existing_index.bedrock.bedrock_knowledge_base_id = "kb-1"
    mock_get_by_bedrock_aws_settings_id.return_value = [existing_index]

    mock_bedrock_get_knowledge_base.return_value = kb_detail_vector

    result, next_token = BedrockKnowledgeBaseService.list_importable_entities_for_main_entity(
        mock_user, "kb-1", "setting-1", page=0, per_page=10
    )

    assert len(result) == 1
    assert result[0]["id"] == "kb-1"
    assert result[0]["aiRunId"] == "index-123"
    assert next_token is None


@patch("codemie.service.aws_bedrock.bedrock_knowledge_base_service.get_setting_for_user")
@patch("codemie.service.aws_bedrock.bedrock_knowledge_base_service.get_setting_aws_credentials")
@patch("codemie.rest_api.models.index.IndexInfo.get_by_bedrock_aws_settings_id")
@patch(
    "codemie.service.aws_bedrock.bedrock_knowledge_base_service.BedrockKnowledgeBaseService._bedrock_get_knowledge_base"
)
def test_list_importable_entities_for_main_entity_not_found(
    mock_bedrock_get_knowledge_base,
    mock_get_by_bedrock_aws_settings_id,
    mock_get_setting_aws_credentials,
    mock_get_setting_for_user,
    mock_user,
    mock_setting,
    mock_aws_creds,
):
    """Test list_importable_entities_for_main_entity when knowledge base is not found."""
    mock_get_setting_for_user.return_value = mock_setting
    mock_get_setting_aws_credentials.return_value = mock_aws_creds
    mock_get_by_bedrock_aws_settings_id.return_value = []
    mock_bedrock_get_knowledge_base.return_value = None

    with pytest.raises(ExtendedHTTPException):
        BedrockKnowledgeBaseService.list_importable_entities_for_main_entity(
            mock_user, "kb-1", "setting-1", page=0, per_page=10
        )


@patch("codemie.service.aws_bedrock.bedrock_knowledge_base_service.get_setting_for_user")
@patch("codemie.service.aws_bedrock.bedrock_knowledge_base_service.get_setting_aws_credentials")
@patch("codemie.rest_api.models.index.IndexInfo.get_by_bedrock_aws_settings_id")
@patch(
    "codemie.service.aws_bedrock.bedrock_knowledge_base_service.BedrockKnowledgeBaseService._bedrock_get_knowledge_base"
)
def test_list_importable_entities_for_main_entity_not_active(
    mock_bedrock_get_knowledge_base,
    mock_get_by_bedrock_aws_settings_id,
    mock_get_setting_aws_credentials,
    mock_get_setting_for_user,
    mock_user,
    mock_setting,
    mock_aws_creds,
):
    """Test list_importable_entities_for_main_entity with non-active knowledge base."""
    mock_get_setting_for_user.return_value = mock_setting
    mock_get_setting_aws_credentials.return_value = mock_aws_creds
    mock_get_by_bedrock_aws_settings_id.return_value = []

    kb_detail_vector = {
        "knowledgeBaseId": "kb-1",
        "name": "KB 1",
        "status": "CREATING",
        "description": "desc",
        "knowledgeBaseConfiguration": {
            "type": "VECTOR",
            "vectorKnowledgeBaseConfiguration": {
                "embeddingModelArn": "arn:aws:bedrock:us-east-1::foundation-model/amazon.titan-embed-text-v1"
            },
        },
        "createdAt": "2024-01-01T00:00:00Z",
        "updatedAt": "2024-01-02T00:00:00Z",
    }
    mock_bedrock_get_knowledge_base.return_value = kb_detail_vector

    result, next_token = BedrockKnowledgeBaseService.list_importable_entities_for_main_entity(
        mock_user, "kb-1", "setting-1", page=0, per_page=10
    )

    assert len(result) == 1
    assert result[0]["id"] == "kb-1"
    assert result[0]["type"] == "VECTOR"
    assert result[0]["embeddingModel"] == "amazon.titan-embed-text-v1"
    assert next_token is None


# --- Tests for get_importable_entity_detail ---
@patch("codemie.service.aws_bedrock.bedrock_knowledge_base_service.get_setting_for_user")
@patch("codemie.service.aws_bedrock.bedrock_knowledge_base_service.get_setting_aws_credentials")
@patch(
    "codemie.service.aws_bedrock.bedrock_knowledge_base_service.BedrockKnowledgeBaseService._bedrock_get_knowledge_base"
)
def test_get_importable_entity_detail_success(
    mock_bedrock_get_knowledge_base,
    mock_get_setting_aws_credentials,
    mock_get_setting_for_user,
    mock_user,
    mock_setting,
    mock_aws_creds,
    kb_detail_vector,
):
    """Test get_importable_entity_detail returns correct knowledge base information."""
    mock_get_setting_for_user.return_value = mock_setting
    mock_get_setting_aws_credentials.return_value = mock_aws_creds
    mock_bedrock_get_knowledge_base.return_value = kb_detail_vector

    result = BedrockKnowledgeBaseService.get_importable_entity_detail(mock_user, "kb-1", "not-used", "setting-1")

    assert result["id"] == "kb-1"
    assert result["name"] == "KB 1"
    assert result["description"] == "desc"
    assert result["status"] == "ACTIVE"
    assert result["arn"] == "arn:aws:bedrock:us-east-1:123456789012:knowledge-base/kb-1"
    assert result["roleArn"] == "arn:aws:iam::123456789012:role/BedrockRole"
    assert "knowledgeBaseConfiguration" in result
    assert "storageConfiguration" in result


@patch("codemie.service.aws_bedrock.bedrock_knowledge_base_service.get_setting_for_user")
@patch("codemie.service.aws_bedrock.bedrock_knowledge_base_service.get_setting_aws_credentials")
@patch(
    "codemie.service.aws_bedrock.bedrock_knowledge_base_service.BedrockKnowledgeBaseService._bedrock_get_knowledge_base"
)
def test_get_importable_entity_detail_not_found(
    mock_bedrock_get_knowledge_base,
    mock_get_setting_aws_credentials,
    mock_get_setting_for_user,
    mock_user,
    mock_setting,
    mock_aws_creds,
):
    """Test get_importable_entity_detail when knowledge base is not found."""
    mock_get_setting_for_user.return_value = mock_setting
    mock_get_setting_aws_credentials.return_value = mock_aws_creds
    mock_bedrock_get_knowledge_base.return_value = None

    result = BedrockKnowledgeBaseService.get_importable_entity_detail(mock_user, "kb-1", "not-used", "setting-1")

    assert result == {}


# --- Tests for import_entities ---
@patch("codemie.service.aws_bedrock.bedrock_knowledge_base_service.get_setting_for_user")
@patch("codemie.service.aws_bedrock.bedrock_knowledge_base_service.get_setting_aws_credentials")
@patch("codemie.rest_api.models.index.IndexInfo.get_by_bedrock_aws_settings_id")
@patch("codemie.service.aws_bedrock.bedrock_knowledge_base_service.BedrockKnowledgeBaseService._process_entity_import")
def test_import_entities_success(
    mock_process_entity_import,
    mock_get_by_bedrock_aws_settings_id,
    mock_get_setting_aws_credentials,
    mock_get_setting_for_user,
    mock_user,
    mock_setting,
    kb_import,
):
    """Test import_entities calls _process_entity_import correctly."""
    setting_id = "setting-1"
    import_payload = {setting_id: [kb_import]}
    mock_get_setting_for_user.return_value = mock_setting
    mock_get_setting_aws_credentials.return_value = MagicMock(spec=AWSCredentials)
    mock_get_by_bedrock_aws_settings_id.return_value = []
    mock_process_entity_import.return_value = {"knowledgeBaseId": "kb-1", "aiRunId": "run-123"}

    result = BedrockKnowledgeBaseService.import_entities(mock_user, import_payload)

    assert len(result) == 1
    assert result[0]["knowledgeBaseId"] == "kb-1"
    assert result[0]["aiRunId"] == "run-123"
    mock_process_entity_import.assert_called_once_with(
        user=mock_user,
        setting=mock_setting,
        aws_creds=mock_get_setting_aws_credentials.return_value,
        existing_entities_map={},
        knowledge_base_id="kb-1",
    )


# --- Tests for delete_entities ---
@patch("codemie.rest_api.models.index.IndexInfo.get_by_bedrock_aws_settings_id")
@patch("codemie.service.guardrail.guardrail_service.GuardrailService.remove_guardrail_assignments_for_entity")
def test_delete_entities_deletes_all_indexes(mock_remove_guardrails, mock_get_by_bedrock_aws_settings_id):
    """Test delete_entities deletes all indexes for a given setting_id."""
    mock_remove_guardrails.return_value = None

    mock_index1 = MagicMock()
    mock_index2 = MagicMock()
    mock_get_by_bedrock_aws_settings_id.return_value = [mock_index1, mock_index2]

    BedrockKnowledgeBaseService.delete_entities("setting-1")

    mock_index1.delete.assert_called_once()
    mock_index2.delete.assert_called_once()
    mock_get_by_bedrock_aws_settings_id.assert_called_once_with("setting-1")


# --- Tests for invoke_knowledge_base ---
@patch("codemie.rest_api.models.index.IndexInfo.get_by_id")
@patch("codemie.rest_api.models.settings.Settings.get_by_id")
@patch("codemie.service.aws_bedrock.bedrock_knowledge_base_service.get_setting_aws_credentials")
@patch(
    "codemie.service.aws_bedrock.bedrock_knowledge_base_service.BedrockKnowledgeBaseService._bedrock_retrieve_knowledge_base"
)
def test_invoke_knowledge_base_success(
    mock_bedrock_retrieve_knowledge_base,
    mock_get_setting_aws_credentials,
    mock_settings_get_by_id,
    mock_indexinfo_get_by_id,
    mock_user,
    kb_index,
    mock_setting,
):
    """Test invoke_knowledge_base successfully retrieves from knowledge base."""
    kb_index.bedrock.bedrock_knowledge_base_id = "kb-1"
    kb_index.setting_id = "setting-1"
    kb_index.created_by = MagicMock(id="user-id", username="testuser", name="Test User")
    mock_indexinfo_get_by_id.return_value = kb_index
    mock_settings_get_by_id.return_value = mock_setting
    mock_get_setting_aws_credentials.return_value = AWSCredentials(
        region="us-east-1",
        access_key_id="test-access-key",
        secret_access_key="test-secret-key",
    )
    mock_bedrock_retrieve_knowledge_base.return_value = [{"content": {"text": "result"}}]

    result = BedrockKnowledgeBaseService.invoke_knowledge_base("query", bedrock_index_info_id="index-1")

    assert isinstance(result, list)
    assert result[0]["content"]["text"] == "result"
    mock_bedrock_retrieve_knowledge_base.assert_called_once_with(
        input_text="query",
        bedrock_knowledge_base_id="kb-1",
        region="us-east-1",
        access_key_id="test-access-key",
        secret_access_key="test-secret-key",
        session_token=None,
    )


@patch("codemie.rest_api.models.index.IndexInfo.get_by_id")
@patch("codemie.rest_api.models.settings.Settings.get_by_id")
def test_invoke_knowledge_base_missing_setting(
    mock_settings_get_by_id,
    mock_indexinfo_get_by_id,
    kb_index,
):
    """Test invoke_knowledge_base when setting is not found."""
    kb_index.setting_id = "setting-1"
    mock_indexinfo_get_by_id.return_value = kb_index
    mock_settings_get_by_id.return_value = None

    with pytest.raises(ValueError) as exc:
        BedrockKnowledgeBaseService.invoke_knowledge_base("query", bedrock_index_info_id="index-1")
    assert "Missing setting" in str(exc.value)


@patch("codemie.rest_api.models.index.IndexInfo.get_by_id")
@patch("codemie.rest_api.models.settings.Settings.get_by_id")
def test_invoke_knowledge_base_missing_created_by(
    mock_settings_get_by_id,
    mock_indexinfo_get_by_id,
    kb_index,
    mock_setting,
):
    """Test invoke_knowledge_base when created_by is missing."""
    kb_index.setting_id = "setting-1"
    kb_index.created_by = None
    mock_indexinfo_get_by_id.return_value = kb_index
    mock_settings_get_by_id.return_value = mock_setting

    with pytest.raises(ValueError) as exc:
        BedrockKnowledgeBaseService.invoke_knowledge_base("query", bedrock_index_info_id="index-1")
    assert "Missing created_by user" in str(exc.value)


@patch("codemie.rest_api.models.index.IndexInfo.get_by_id")
@patch("codemie.rest_api.models.settings.Settings.get_by_id")
@patch("codemie.service.aws_bedrock.bedrock_knowledge_base_service.get_setting_aws_credentials")
def test_invoke_knowledge_base_missing_kb_id(
    mock_get_setting_aws_credentials,
    mock_settings_get_by_id,
    mock_indexinfo_get_by_id,
    kb_index,
    mock_setting,
):
    """Test invoke_knowledge_base when knowledge base ID is missing."""
    kb_index.bedrock = None
    kb_index.setting_id = "setting-1"
    kb_index.created_by = MagicMock(id="user-id", username="testuser", name="Test User")
    mock_indexinfo_get_by_id.return_value = kb_index
    mock_settings_get_by_id.return_value = mock_setting
    mock_get_setting_aws_credentials.return_value = AWSCredentials(
        region="us-east-1",
        access_key_id="test-access-key",
        secret_access_key="test-secret-key",
    )

    with pytest.raises(ValueError) as exc:
        BedrockKnowledgeBaseService.invoke_knowledge_base("query", bedrock_index_info_id="index-1")
    assert "Missing bedrock_knowledge_base_id" in str(exc.value)


# --- Tests for validate_remote_entity_exists_and_cleanup ---
@patch("codemie.rest_api.models.settings.Settings.get_by_id")
@patch("codemie.service.aws_bedrock.bedrock_knowledge_base_service.get_setting_aws_credentials")
@patch(
    "codemie.service.aws_bedrock.bedrock_knowledge_base_service.BedrockKnowledgeBaseService._bedrock_get_knowledge_base"
)
def test_validate_remote_entity_exists_and_cleanup_success(
    mock_bedrock_get_knowledge_base,
    mock_get_setting_aws_credentials,
    mock_settings_get_by_id,
    mock_aws_creds,
):
    """Test validate_remote_entity_exists_and_cleanup when knowledge base exists remotely."""
    # Create mock index with Bedrock configuration
    mock_index = MagicMock()
    mock_index.bedrock.bedrock_knowledge_base_id = "kb-123"
    mock_index.bedrock.bedrock_aws_settings_id = "setting-789"
    mock_index.full_name = "Test Knowledge Base"

    mock_setting = MagicMock()
    mock_settings_get_by_id.return_value = mock_setting
    mock_get_setting_aws_credentials.return_value = mock_aws_creds
    mock_bedrock_get_knowledge_base.return_value = {"knowledgeBaseId": "kb-123"}

    result = BedrockKnowledgeBaseService.validate_remote_entity_exists_and_cleanup(mock_index)

    assert result is None
    mock_settings_get_by_id.assert_called_once_with("setting-789")  # Remove id_= parameter
    mock_get_setting_aws_credentials.assert_called_once_with(mock_setting.id)
    mock_bedrock_get_knowledge_base.assert_called_once_with(
        knowledge_base_id="kb-123",
        region=mock_aws_creds.region,
        access_key_id=mock_aws_creds.access_key_id,
        secret_access_key=mock_aws_creds.secret_access_key,
        session_token=mock_aws_creds.session_token,
    )
    mock_index.delete.assert_not_called()


@patch("codemie.rest_api.models.settings.Settings.get_by_id")
@patch("codemie.service.aws_bedrock.bedrock_knowledge_base_service.get_setting_aws_credentials")
@patch(
    "codemie.service.aws_bedrock.bedrock_knowledge_base_service.BedrockKnowledgeBaseService._bedrock_get_knowledge_base"
)
@patch("codemie.service.guardrail.guardrail_service.GuardrailService.remove_guardrail_assignments_for_entity")
def test_validate_remote_entity_exists_and_cleanup_resource_not_found(
    mock_remove_guardrails,
    mock_bedrock_get_knowledge_base,
    mock_get_setting_aws_credentials,
    mock_settings_get_by_id,
    mock_aws_creds,
):
    """Test validate_remote_entity_exists_and_cleanup when knowledge base is deleted remotely."""
    from botocore.exceptions import ClientError

    mock_remove_guardrails.return_value = None

    # Create mock index with Bedrock configuration
    mock_index = MagicMock()
    mock_index.bedrock.bedrock_knowledge_base_id = "kb-123"
    mock_index.bedrock.bedrock_aws_settings_id = "setting-789"
    mock_index.full_name = "Test Knowledge Base"

    mock_setting = MagicMock()
    mock_settings_get_by_id.return_value = mock_setting
    mock_get_setting_aws_credentials.return_value = mock_aws_creds

    # Mock ResourceNotFoundException
    error_response = {"Error": {"Code": "ResourceNotFoundException", "Message": "Knowledge base not found"}}
    mock_bedrock_get_knowledge_base.side_effect = ClientError(error_response, "GetKnowledgeBase")

    result = BedrockKnowledgeBaseService.validate_remote_entity_exists_and_cleanup(mock_index)

    assert result == "Test Knowledge Base"
    mock_index.delete.assert_called_once()


@patch("codemie.rest_api.models.settings.Settings.get_by_id")
@patch("codemie.service.aws_bedrock.bedrock_knowledge_base_service.get_setting_aws_credentials")
@patch(
    "codemie.service.aws_bedrock.bedrock_knowledge_base_service.BedrockKnowledgeBaseService._bedrock_get_knowledge_base"
)
def test_validate_remote_entity_exists_and_cleanup_other_client_error_passes(
    mock_bedrock_get_knowledge_base,
    mock_get_setting_aws_credentials,
    mock_settings_get_by_id,
    mock_aws_creds,
):
    """Test validate_remote_entity_exists_and_cleanup with other AWS client errors."""
    from botocore.exceptions import ClientError

    # Create mock index with Bedrock configuration
    mock_index = MagicMock()
    mock_index.bedrock.bedrock_knowledge_base_id = "kb-123"
    mock_index.bedrock.bedrock_aws_settings_id = "setting-789"
    mock_index.full_name = "Test Knowledge Base"

    mock_setting = MagicMock()
    mock_settings_get_by_id.return_value = mock_setting
    mock_get_setting_aws_credentials.return_value = mock_aws_creds

    # Mock other client error
    error_response = {"Error": {"Code": "AccessDeniedException", "Message": "Access denied"}}
    mock_bedrock_get_knowledge_base.side_effect = ClientError(error_response, "GetKnowledgeBase")

    BedrockKnowledgeBaseService.validate_remote_entity_exists_and_cleanup(mock_index)

    mock_index.delete.assert_not_called()


def test_validate_remote_entity_exists_and_cleanup_non_bedrock_index():
    """Test validate_remote_entity_exists_and_cleanup with non-Bedrock index."""
    # Create mock index without Bedrock configuration
    mock_index = MagicMock()
    mock_index.bedrock = None

    result = BedrockKnowledgeBaseService.validate_remote_entity_exists_and_cleanup(mock_index)

    assert result is None
    mock_index.delete.assert_not_called()


def test_validate_remote_entity_exists_and_cleanup_missing_bedrock_fields():
    """Test validate_remote_entity_exists_and_cleanup with incomplete Bedrock configuration."""
    # Create mock index with incomplete Bedrock configuration
    mock_index = MagicMock()
    mock_index.bedrock.bedrock_knowledge_base_id = None

    result = BedrockKnowledgeBaseService.validate_remote_entity_exists_and_cleanup(mock_index)

    assert result is None
    mock_index.delete.assert_not_called()


@patch("codemie.rest_api.models.settings.Settings.get_by_id")
def test_validate_remote_entity_exists_and_cleanup_setting_not_found_passes(
    mock_settings_get_by_id,
):
    """Test validate_remote_entity_exists_and_cleanup when setting is not found."""
    # Create mock index with Bedrock configuration
    mock_index = MagicMock()
    mock_index.bedrock.bedrock_knowledge_base_id = "kb-123"
    mock_index.bedrock.bedrock_aws_settings_id = "setting-789"

    mock_settings_get_by_id.return_value = None

    BedrockKnowledgeBaseService.validate_remote_entity_exists_and_cleanup(mock_index)

    mock_index.delete.assert_not_called()


@patch("codemie.rest_api.models.settings.Settings.get_by_id")
def test_validate_remote_entity_exists_and_cleanup_unexpected_error_passes(
    mock_settings_get_by_id,
):
    """Test validate_remote_entity_exists_and_cleanup with unexpected errors."""
    # Create mock index with Bedrock configuration
    mock_index = MagicMock()
    mock_index.bedrock.bedrock_knowledge_base_id = "kb-123"
    mock_index.bedrock.bedrock_aws_settings_id = "setting-789"
    mock_index.full_name = "Test Knowledge Base"

    mock_settings_get_by_id.side_effect = Exception("Unexpected error")

    BedrockKnowledgeBaseService.validate_remote_entity_exists_and_cleanup(mock_index)

    mock_index.delete.assert_not_called()
