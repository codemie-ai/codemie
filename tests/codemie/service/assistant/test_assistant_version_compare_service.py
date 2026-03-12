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

"""Unit tests for AssistantVersionCompareService"""

from datetime import datetime, UTC
from unittest.mock import MagicMock, patch

import pytest
from deepdiff import DeepDiff

from codemie.core.models import CreatedByUser
from codemie.rest_api.models.assistant import (
    AssistantConfiguration,
    AssistantVersionCompareResponse,
    Context,
    ContextType,
    ToolKitDetails,
)
from codemie.service.assistant.assistant_version_compare_service import AssistantVersionCompareService


@pytest.fixture
def mock_config_v1():
    """Mock configuration version 1"""
    config = MagicMock(spec=AssistantConfiguration)
    config.id = "config-1"
    config.assistant_id = "assistant-123"
    config.version_number = 1
    config.description = "Original Description"
    config.system_prompt = "Original Prompt"
    config.llm_model_type = "gpt-3.5-turbo"
    config.temperature = 0.7
    config.top_p = 0.9
    config.context = []
    config.toolkits = []
    config.mcp_servers = []
    config.assistant_ids = []
    config.conversation_starters = []
    config.bedrock = None
    config.agent_card = None
    config.created_date = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
    config.created_by = CreatedByUser(id="user-1", username="user1", name="User One")
    config.change_notes = "Initial version"
    config.model_dump = MagicMock(
        return_value={
            'id': 'config-1',
            'assistant_id': 'assistant-123',
            'version_number': 1,
            'description': 'Original Description',
            'system_prompt': 'Original Prompt',
            'llm_model_type': 'gpt-3.5-turbo',
            'temperature': 0.7,
            'top_p': 0.9,
            'context': [],
            'toolkits': [],
            'mcp_servers': [],
            'assistant_ids': [],
            'conversation_starters': [],
            'bedrock': None,
            'agent_card': None,
            'created_date': datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC),
            'created_by': CreatedByUser(id="user-1", username="user1", name="User One"),
            'change_notes': 'Initial version',
        }
    )
    return config


@pytest.fixture
def mock_config_v2():
    """Mock configuration version 2"""
    config = MagicMock(spec=AssistantConfiguration)
    config.id = "config-2"
    config.assistant_id = "assistant-123"
    config.version_number = 2
    config.description = "Updated Description"
    config.system_prompt = "Updated Prompt"
    config.llm_model_type = "gpt-4"
    config.temperature = 0.8
    config.top_p = 0.95
    config.context = [Context(name="test-repo", context_type=ContextType.CODE)]
    config.toolkits = [ToolKitDetails(toolkit="General", tools=[], label="General Tools")]
    config.mcp_servers = []
    config.assistant_ids = []
    config.conversation_starters = ["Hello!", "Hi!"]
    config.bedrock = None
    config.agent_card = None
    config.created_date = datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC)
    config.created_by = CreatedByUser(id="user-2", username="user2", name="User Two")
    config.change_notes = "Updated configuration"
    config.model_dump = MagicMock(
        return_value={
            'id': 'config-2',
            'assistant_id': 'assistant-123',
            'version_number': 2,
            'description': 'Updated Description',
            'system_prompt': 'Updated Prompt',
            'llm_model_type': 'gpt-4',
            'temperature': 0.8,
            'top_p': 0.95,
            'context': [Context(name="test-repo", context_type=ContextType.CODE)],
            'toolkits': [ToolKitDetails(toolkit="General", tools=[], label="General Tools")],
            'mcp_servers': [],
            'assistant_ids': [],
            'conversation_starters': ['Hello!', 'Hi!'],
            'bedrock': None,
            'agent_card': None,
            'created_date': datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC),
            'created_by': CreatedByUser(id="user-2", username="user2", name="User Two"),
            'change_notes': 'Updated configuration',
        }
    )
    return config


class TestCompareVersions:
    """Tests for compare_versions method"""

    def test_compare_versions_with_differences(self, mock_config_v1, mock_config_v2):
        """Test comparison of two different versions"""
        # Execute
        result = AssistantVersionCompareService.compare_versions(
            assistant_id="assistant-123", version1=mock_config_v1, version2=mock_config_v2
        )

        # Verify
        assert isinstance(result, AssistantVersionCompareResponse)
        assert result.assistant_id == "assistant-123"
        assert result.version1 == mock_config_v1
        assert result.version2 == mock_config_v2
        assert isinstance(result.differences, dict)
        assert result.change_summary is not None
        assert "Changes from version 1 to version 2" in result.change_summary

    def test_compare_identical_versions(self, mock_config_v1):
        """Test comparison of identical versions shows no differences"""
        # Execute
        result = AssistantVersionCompareService.compare_versions(
            assistant_id="assistant-123", version1=mock_config_v1, version2=mock_config_v1
        )

        # Verify
        assert result.differences == {}
        assert "No differences" in result.change_summary

    def test_compare_versions_calls_generate_summary(self, mock_config_v1, mock_config_v2):
        """Test that _generate_summary is called with diff result"""
        # Execute
        with patch.object(
            AssistantVersionCompareService, '_generate_summary', return_value="Test Summary"
        ) as mock_summary:
            result = AssistantVersionCompareService.compare_versions(
                assistant_id="assistant-123", version1=mock_config_v1, version2=mock_config_v2
            )

            # Verify
            mock_summary.assert_called_once()
            assert result.change_summary == "Test Summary"


class TestPrepareForComparison:
    """Tests for _prepare_for_comparison method"""

    def test_prepare_for_comparison_excludes_metadata_fields(self, mock_config_v1):
        """Test that metadata fields are excluded from comparison"""
        # Execute
        result = AssistantVersionCompareService._prepare_for_comparison(mock_config_v1)

        # Verify excluded fields are not present
        excluded_fields = {'id', 'assistant_id', 'version_number', 'created_date', 'created_by', 'change_notes'}
        for field in excluded_fields:
            assert field not in result

    def test_prepare_for_comparison_includes_config_fields(self, mock_config_v1):
        """Test that configuration fields are included"""
        # Execute
        result = AssistantVersionCompareService._prepare_for_comparison(mock_config_v1)

        # Verify included fields are present
        included_fields = {
            'description',
            'system_prompt',
            'llm_model_type',
            'temperature',
            'top_p',
            'context',
            'toolkits',
            'mcp_servers',
            'assistant_ids',
            'conversation_starters',
        }
        for field in included_fields:
            assert field in result

    def test_prepare_for_comparison_returns_dict(self, mock_config_v1):
        """Test that result is a dictionary"""
        # Execute
        result = AssistantVersionCompareService._prepare_for_comparison(mock_config_v1)

        # Verify
        assert isinstance(result, dict)


class TestGenerateSummary:
    """Tests for _generate_summary method"""

    def test_generate_summary_no_differences(self):
        """Test summary for identical versions"""
        # Setup
        diff = DeepDiff({}, {})

        # Execute
        result = AssistantVersionCompareService._generate_summary(diff, 1, 2)

        # Verify
        assert "No differences between version 1 and version 2" in result

    def test_generate_summary_with_values_changed(self):
        """Test summary when values are changed"""
        # Setup
        dict1 = {'description': 'Old', 'system_prompt': 'Old Prompt', 'temperature': 0.7}
        dict2 = {'description': 'New', 'system_prompt': 'New Prompt', 'temperature': 0.8}
        diff = DeepDiff(dict1, dict2)

        # Execute
        result = AssistantVersionCompareService._generate_summary(diff, 1, 2)

        # Verify
        assert "Changes from version 1 to version 2" in result
        assert "field(s) modified" in result

    def test_generate_summary_with_items_added(self):
        """Test summary when items are added to lists"""
        # Setup
        dict1 = {'conversation_starters': []}
        dict2 = {'conversation_starters': ['Hello!', 'Hi!']}
        diff = DeepDiff(dict1, dict2)

        # Execute
        result = AssistantVersionCompareService._generate_summary(diff, 1, 2)

        # Verify
        assert "Changes from version 1 to version 2" in result
        assert "item(s) added" in result

    def test_generate_summary_with_items_removed(self):
        """Test summary when items are removed from lists"""
        # Setup
        dict1 = {'conversation_starters': ['Hello!', 'Hi!']}
        dict2 = {'conversation_starters': []}
        diff = DeepDiff(dict1, dict2)

        # Execute
        result = AssistantVersionCompareService._generate_summary(diff, 1, 2)

        # Verify
        assert "Changes from version 1 to version 2" in result
        assert "item(s) removed" in result

    def test_generate_summary_with_dictionary_items_added(self):
        """Test summary when dictionary fields are added"""
        # Setup
        dict1 = {'description': 'Test'}
        dict2 = {'description': 'Test', 'system_prompt': 'New'}
        diff = DeepDiff(dict1, dict2)

        # Execute
        result = AssistantVersionCompareService._generate_summary(diff, 1, 2)

        # Verify
        assert "Changes from version 1 to version 2" in result
        assert "new field(s)" in result

    def test_generate_summary_with_dictionary_items_removed(self):
        """Test summary when dictionary fields are removed"""
        # Setup
        dict1 = {'description': 'Test', 'system_prompt': 'Old'}
        dict2 = {'description': 'Test'}
        diff = DeepDiff(dict1, dict2)

        # Execute
        result = AssistantVersionCompareService._generate_summary(diff, 1, 2)

        # Verify
        assert "Changes from version 1 to version 2" in result
        assert "field(s) removed" in result

    def test_generate_summary_multiple_change_types(self):
        """Test summary with multiple types of changes"""
        # Setup
        dict1 = {
            'description': 'Old Description',
            'temperature': 0.7,
            'conversation_starters': ['Hi'],
            'toolkits': [],
        }
        dict2 = {
            'description': 'New Description',
            'temperature': 0.8,
            'conversation_starters': ['Hello!', 'Hi there!'],
            'toolkits': [],
            'new_field': 'value',
        }
        diff = DeepDiff(dict1, dict2)

        # Execute
        result = AssistantVersionCompareService._generate_summary(diff, 1, 3)

        # Verify
        assert "Changes from version 1 to version 3" in result
        assert "modified" in result or "added" in result or "new field" in result

    def test_generate_summary_limits_field_list(self):
        """Test that summary limits the number of changed fields displayed"""
        # Setup - create dict with many changed fields
        dict1 = {f'field_{i}': f'old_{i}' for i in range(10)}
        dict2 = {f'field_{i}': f'new_{i}' for i in range(10)}
        diff = DeepDiff(dict1, dict2)

        # Execute
        result = AssistantVersionCompareService._generate_summary(diff, 1, 2)

        # Verify - should only show first 3 fields
        assert "field(s) modified" in result
        field_mentions = result.count('field_')
        assert field_mentions <= 3  # Only first 3 fields should be mentioned


class TestIntegration:
    """Integration tests for the full comparison flow"""

    def test_full_comparison_workflow(self, mock_config_v1, mock_config_v2):
        """Test complete comparison workflow"""
        # Execute
        result = AssistantVersionCompareService.compare_versions(
            assistant_id="assistant-123", version1=mock_config_v1, version2=mock_config_v2
        )

        # Verify complete response structure
        assert result.assistant_id == "assistant-123"
        assert result.version1.version_number == 1
        assert result.version2.version_number == 2
        assert isinstance(result.differences, dict)
        assert isinstance(result.change_summary, str)
        assert len(result.change_summary) > 0
