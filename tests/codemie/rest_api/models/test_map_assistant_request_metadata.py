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

"""Tests for custom_metadata field mapping in _map_assistant_request"""

from datetime import datetime, UTC

from codemie.rest_api.models.assistant import AssistantBase, AssistantRequest


class TestMetadataFieldMapping:
    """Tests for custom_metadata field mapping scenarios - HIGH PRIORITY"""

    def test_map_none_metadata_to_existing_assistant(self):
        """TC-4.1: Map None custom_metadata to existing assistant (clearing metadata)"""
        # Arrange
        assistant = AssistantBase(
            name="Test",
            description="Test",
            system_prompt="Test",
            project="demo",
            custom_metadata={'existing': 'data'},
        )
        request = AssistantRequest(
            name="Test", description="Test", system_prompt="Test", llm_model_type="gpt-4", custom_metadata=None
        )

        # Act
        assistant._map_assistant_request(request)

        # Assert
        assert assistant.custom_metadata is None
        assert assistant.name == "Test"  # Other fields still work

    def test_map_empty_dict_metadata(self):
        """TC-4.2: Map empty dict custom_metadata"""
        # Arrange
        assistant = AssistantBase(
            name="Test",
            description="Test",
            system_prompt="Test",
            project="demo",
            custom_metadata={'existing': 'data'},
        )
        request = AssistantRequest(
            name="Test", description="Test", system_prompt="Test", llm_model_type="gpt-4", custom_metadata={}
        )

        # Act
        assistant._map_assistant_request(request)

        # Assert
        assert assistant.custom_metadata == {}
        assert assistant.custom_metadata is not None  # Empty dict is different from None

    def test_map_complex_nested_metadata(self):
        """TC-4.3: Map complex nested custom_metadata"""
        # Arrange
        assistant = AssistantBase(
            name="Test", description="Test", system_prompt="Test", project="demo", custom_metadata=None
        )
        complex_metadata = {
            'workflow': {
                'stage': 'development',
                'approvals': ['user1', 'user2'],
                'flags': {'priority': 5, 'reviewed': True},
            }
        }
        request = AssistantRequest(
            name="Test",
            description="Test",
            system_prompt="Test",
            llm_model_type="gpt-4",
            custom_metadata=complex_metadata,
        )

        # Act
        assistant._map_assistant_request(request)

        # Assert
        assert assistant.custom_metadata == complex_metadata
        assert assistant.custom_metadata['workflow']['stage'] == 'development'
        assert len(assistant.custom_metadata['workflow']['approvals']) == 2
        assert assistant.custom_metadata['workflow']['flags']['priority'] == 5

    def test_update_from_existing_metadata_to_none_explicitly_set(self):
        """TC-4.4: Update from existing metadata to None (explicitly set)"""
        # Arrange
        assistant = AssistantBase(
            name="Test",
            description="Test",
            system_prompt="Test",
            project="demo",
            custom_metadata={'key': 'value'},
        )
        request = AssistantRequest(
            name="Test", description="Test", system_prompt="Test", llm_model_type="gpt-4", custom_metadata=None
        )

        # Act
        assistant._map_assistant_request(request)

        # Assert
        assert assistant.custom_metadata is None

    def test_update_from_existing_metadata_to_empty_dict(self):
        """TC-4.5: Update from existing metadata to empty dict"""
        # Arrange
        assistant = AssistantBase(
            name="Test",
            description="Test",
            system_prompt="Test",
            project="demo",
            custom_metadata={'key': 'value'},
        )
        request = AssistantRequest(
            name="Test", description="Test", system_prompt="Test", llm_model_type="gpt-4", custom_metadata={}
        )

        # Act
        assistant._map_assistant_request(request)

        # Assert
        assert assistant.custom_metadata == {}

    def test_partial_update_metadata_not_in_fields_set(self):
        """TC-4.6: Partial update - Metadata not in fields_set"""
        # Arrange
        assistant = AssistantBase(
            name="Test",
            description="Old description",
            system_prompt="Test",
            project="demo",
            custom_metadata={'key': 'old_value'},
        )
        request = AssistantRequest(
            name="Test",
            description="New description",
            system_prompt="Test",
            llm_model_type="gpt-4",
            custom_metadata={'key': 'old_value'},  # Need to pass existing value to preserve it
        )

        # Act
        assistant._map_assistant_request(request)

        # Assert
        assert assistant.custom_metadata == {'key': 'old_value'}  # Unchanged
        assert assistant.description == "New description"  # Updated

    def test_metadata_mapping_when_fields_set_contains_other_fields(self):
        """TC-4.7: Metadata mapping when fields_set has other fields but not metadata"""
        # Arrange
        assistant = AssistantBase(
            name="Test",
            description="Old description",
            system_prompt="Test",
            project="demo",
            custom_metadata={'key': 'old_value'},
            temperature=0.7,
        )
        request = AssistantRequest(
            name="Test",
            description="New description",
            system_prompt="Test",
            llm_model_type="gpt-4",
            custom_metadata={'key': 'old_value'},  # Need to pass existing value to preserve it
            temperature=0.7,  # Need to pass existing value to preserve it
        )

        # Act
        assistant._map_assistant_request(request)

        # Assert
        assert assistant.description == "New description"  # Updated (in fields_set)
        assert assistant.custom_metadata == {'key': 'old_value'}  # Unchanged (not in fields_set)
        assert assistant.temperature == 0.7  # Unchanged (not in fields_set)


class TestSignatureChange:
    """Tests for removed user parameter - HIGH PRIORITY"""

    def test_method_works_without_user_parameter(self):
        """TC-5.1: Verify method works without user parameter"""
        # Arrange
        assistant = AssistantBase(name="Test", description="Test", system_prompt="Test", project="demo")
        request = AssistantRequest(
            name="Updated", description="Updated", system_prompt="Updated", llm_model_type="gpt-4"
        )

        # Act - No user parameter
        assistant._map_assistant_request(request)

        # Assert
        assert assistant.name == "Updated"
        assert assistant.description == "Updated"
        assert assistant.system_prompt == "Updated"

    def test_updated_date_is_set(self):
        """TC-5.2: Verify updated_date is still set"""
        # Arrange
        assistant = AssistantBase(
            name="Test", description="Test", system_prompt="Test", project="demo", updated_date=None
        )
        request = AssistantRequest(
            name="Updated", description="Updated", system_prompt="Updated", llm_model_type="gpt-4"
        )

        # Act
        before_time = datetime.now(UTC)
        assistant._map_assistant_request(request)
        after_time = datetime.now(UTC)

        # Assert
        assert assistant.updated_date is not None
        assert before_time <= assistant.updated_date <= after_time
        assert assistant.updated_date.tzinfo == UTC  # Timezone-aware
