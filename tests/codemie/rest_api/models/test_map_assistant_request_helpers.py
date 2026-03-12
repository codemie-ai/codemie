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

"""Tests for _map_assistant_request helper methods"""

from codemie.rest_api.models.assistant import AssistantBase, AssistantRequest


class TestShouldUpdateField:
    """Tests for _should_update_field helper method"""

    def test_should_update_when_fields_set_is_none(self):
        """TC-1.1: Should update field when fields_set is None (legacy mode)"""
        # Arrange
        assistant = AssistantBase(name="Test", description="Test", system_prompt="Test", project="demo")

        # Act & Assert - Legacy mode (fields_set = None)
        assert assistant._should_update_field('description', 'new value', fields_set=None) is True
        assert assistant._should_update_field('custom_metadata', {'key': 'value'}, fields_set=None) is True
        assert assistant._should_update_field('temperature', 0.8, fields_set=None) is True

    def test_should_update_when_field_in_fields_set(self):
        """TC-1.2: Should update field when field is in fields_set"""
        # Arrange
        assistant = AssistantBase(name="Test", description="Test", system_prompt="Test", project="demo")
        fields_set = {'description', 'custom_metadata'}

        # Act & Assert
        assert assistant._should_update_field('description', 'new value', fields_set) is True
        assert assistant._should_update_field('custom_metadata', {'key': 'value'}, fields_set) is True

    def test_should_not_update_when_field_not_in_fields_set_and_value_not_none(self):
        """TC-1.3: Should not update field when not in fields_set and value is not None"""
        # Arrange
        assistant = AssistantBase(name="Test", description="Test", system_prompt="Test", project="demo")
        fields_set = {'description'}

        # Act & Assert
        assert assistant._should_update_field('temperature', 0.8, fields_set) is False
        assert assistant._should_update_field('custom_metadata', {'key': 'value'}, fields_set) is False

    def test_should_update_when_value_is_none(self):
        """TC-1.4: Should update field when value is None regardless of fields_set"""
        # Arrange
        assistant = AssistantBase(name="Test", description="Test", system_prompt="Test", project="demo")
        fields_set = {'description'}  # custom_metadata not in fields_set

        # Act & Assert - None values always processed
        assert assistant._should_update_field('custom_metadata', None, fields_set) is True
        assert assistant._should_update_field('temperature', None, fields_set) is True

    def test_empty_string_vs_none(self):
        """TC-1.5: Edge case - Empty string is treated differently from None"""
        # Arrange
        assistant = AssistantBase(name="Test", description="Test", system_prompt="Test", project="demo")
        fields_set = {'description'}

        # Act & Assert
        assert assistant._should_update_field('system_prompt', '', fields_set) is False
        assert assistant._should_update_field('system_prompt', None, fields_set) is True


class TestGetFieldValueForUpdate:
    """Tests for _get_field_value_for_update helper method"""

    def test_convert_none_to_empty_list_for_prompt_variables(self):
        """TC-2.1: Convert None to empty list for prompt_variables field"""
        # Arrange
        assistant = AssistantBase(name="Test", description="Test", system_prompt="Test", project="demo")

        # Act
        result = assistant._get_field_value_for_update('prompt_variables', None)

        # Assert
        assert result == []

    def test_convert_none_to_empty_list_for_categories(self):
        """TC-2.2: Convert None to empty list for categories field"""
        # Arrange
        assistant = AssistantBase(name="Test", description="Test", system_prompt="Test", project="demo")

        # Act
        result = assistant._get_field_value_for_update('categories', None)

        # Assert
        assert result == []

    def test_return_value_as_is_for_other_fields(self):
        """TC-2.3: Return value as-is for other fields"""
        # Arrange
        assistant = AssistantBase(name="Test", description="Test", system_prompt="Test", project="demo")

        # Act & Assert
        assert assistant._get_field_value_for_update('description', 'test value') == 'test value'
        assert assistant._get_field_value_for_update('temperature', 0.8) == 0.8
        assert assistant._get_field_value_for_update('custom_metadata', {'key': 'value'}) == {'key': 'value'}
        assert assistant._get_field_value_for_update('custom_metadata', None) is None  # Not converted to []

    def test_empty_list_vs_none_for_prompt_variables(self):
        """TC-2.4: Edge case - Empty list vs None for prompt_variables"""
        # Arrange
        assistant = AssistantBase(name="Test", description="Test", system_prompt="Test", project="demo")

        # Act & Assert
        assert assistant._get_field_value_for_update('prompt_variables', []) == []


class TestShouldUpdateSystemPrompt:
    """Tests for _should_update_system_prompt helper method - CRITICAL"""

    def test_should_not_update_when_system_prompt_unchanged(self):
        """TC-3.1: CRITICAL - Should not update when system_prompt unchanged (prevents unnecessary versioning)"""
        # Arrange
        assistant = AssistantBase(name="Test", description="Test", system_prompt="Original Prompt", project="demo")
        request = AssistantRequest(
            name="Test",
            description="Test",
            system_prompt="Original Prompt",  # Same as assistant
            llm_model_type="gpt-4",
        )

        # Act
        result = assistant._should_update_system_prompt(request, fields_set=None)

        # Assert
        assert result is False  # No update needed - prevents unnecessary versioning

    def test_should_update_when_system_prompt_changed_and_fields_set_none(self):
        """TC-3.2: Should update when system_prompt changed and fields_set is None"""
        # Arrange
        assistant = AssistantBase(name="Test", description="Test", system_prompt="Original Prompt", project="demo")
        request = AssistantRequest(
            name="Test",
            description="Test",
            system_prompt="New Prompt",  # Changed
            llm_model_type="gpt-4",
        )

        # Act
        result = assistant._should_update_system_prompt(request, fields_set=None)

        # Assert
        assert result is True

    def test_should_update_when_system_prompt_changed_and_in_fields_set(self):
        """TC-3.3: Should update when system_prompt changed and in fields_set"""
        # Arrange
        assistant = AssistantBase(name="Test", description="Test", system_prompt="Original Prompt", project="demo")
        request = AssistantRequest(
            name="Test",
            description="Test",
            system_prompt="New Prompt",
            llm_model_type="gpt-4",
        )

        # Act
        result = assistant._should_update_system_prompt(request, fields_set={'system_prompt', 'description'})

        # Assert
        assert result is True

    def test_should_not_update_when_system_prompt_changed_but_not_in_fields_set(self):
        """TC-3.4: Should not update when system_prompt changed but not in fields_set"""
        # Arrange
        assistant = AssistantBase(name="Test", description="Test", system_prompt="Original Prompt", project="demo")
        request = AssistantRequest(
            name="Test",
            description="Test",
            system_prompt="New Prompt",  # Changed but not in fields_set
            llm_model_type="gpt-4",
        )

        # Act
        result = assistant._should_update_system_prompt(request, fields_set={'description', 'temperature'})

        # Assert
        assert result is False

    def test_whitespace_changes_detected(self):
        """TC-3.5: Edge case - Whitespace changes in system_prompt"""
        # Arrange
        assistant = AssistantBase(name="Test", description="Test", system_prompt="Original Prompt", project="demo")
        request = AssistantRequest(
            name="Test",
            description="Test",
            system_prompt="Original Prompt ",  # Trailing space
            llm_model_type="gpt-4",
        )

        # Act
        result = assistant._should_update_system_prompt(request, fields_set=None)

        # Assert
        assert result is True  # Whitespace differences detected
