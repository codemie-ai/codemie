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

import pytest

from codemie.rest_api.models.assistant import AssistantBase, AssistantRequest


class TestShouldUpdateField:
    """Tests for _should_update_field helper method"""

    def test_should_update_when_fields_set_is_none(self):
        """TC-1.1: Should update field when fields_set is None (legacy mode)"""
        # Arrange
        assistant = AssistantBase(name="Test", description="Test", system_prompt="Test", project="demo")

        # Act & Assert - Legacy mode (fields_set = None)
        assert assistant._should_update_field('description', fields_set=None) is True
        assert assistant._should_update_field('custom_metadata', fields_set=None) is True
        assert assistant._should_update_field('temperature', fields_set=None) is True

    def test_should_update_when_field_in_fields_set(self):
        """TC-1.2: Should update field when field is explicitly set (present in fields_set)."""
        # Arrange
        assistant = AssistantBase(name="Test", description="Test", system_prompt="Test", project="demo")
        fields_set = {'description', 'custom_metadata', 'temperature'}

        # Act & Assert - explicitly-set fields are applied (so explicit set/clear still works)
        assert assistant._should_update_field('description', fields_set) is True
        assert assistant._should_update_field('custom_metadata', fields_set) is True
        assert assistant._should_update_field('temperature', fields_set) is True

    def test_should_not_update_when_field_not_in_fields_set(self):
        """TC-1.3: A field NOT explicitly set (omitted) must NOT be updated (partial-patch)."""
        # Arrange
        assistant = AssistantBase(name="Test", description="Test", system_prompt="Test", project="demo")
        fields_set = {'description'}  # everything else omitted

        # Act & Assert - omitted fields are preserved regardless of their (default) value
        assert assistant._should_update_field('temperature', fields_set) is False
        assert assistant._should_update_field('custom_metadata', fields_set) is False
        assert assistant._should_update_field('system_prompt', fields_set) is False


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


class TestMapAssistantRequestPartialUpdate:
    """Regression coverage for EPMCDME-14150 partial-update semantics.

    Note: AssistantRequest requires system_prompt for CODEMIE-type assistants, so
    system_prompt is always present in a valid request. The reproducible bug is on
    truly-optional fields such as description; the tests below prove description is
    preserved when omitted while system_prompt round-trips unchanged.
    """

    def test_partial_update_preserves_omitted_optional_fields(self):
        """Omitting description keeps the stored value; unchanged system_prompt is preserved."""
        assistant = AssistantBase(
            name="Test",
            description="Stored description",
            system_prompt="Stored prompt",
            project="demo",
        )
        # description omitted; system_prompt sent unchanged (required for CODEMIE type)
        request = AssistantRequest(name="Test", system_prompt="Stored prompt", llm_model_type="gpt-4")

        assistant._map_assistant_request(request)

        assert assistant.description == "Stored description"
        assert assistant.system_prompt == "Stored prompt"

    def test_explicit_value_is_applied(self):
        """A field explicitly set in the request is applied."""
        assistant = AssistantBase(
            name="Test",
            description="Stored description",
            system_prompt="Stored prompt",
            project="demo",
        )
        request = AssistantRequest(
            name="Test", description="New description", system_prompt="Stored prompt", llm_model_type="gpt-4"
        )

        assistant._map_assistant_request(request)

        assert assistant.description == "New description"

    def test_explicit_null_clears_field(self):
        """A field explicitly set to None in the request is cleared."""
        assistant = AssistantBase(
            name="Test",
            description="Stored description",
            system_prompt="Stored prompt",
            project="demo",
        )
        request = AssistantRequest(name="Test", description=None, system_prompt="Stored prompt", llm_model_type="gpt-4")

        assistant._map_assistant_request(request)

        assert assistant.description is None


@pytest.mark.parametrize("field", ["temperature", "top_p"])
class TestSamplingParamsMapping:
    """EPMCDME-15208: temperature/top_p can be set, updated, cleared and preserved."""

    @staticmethod
    def _assistant(**kwargs):
        return AssistantBase(name="Test", description="Test", system_prompt="Test", project="demo", **kwargs)

    @staticmethod
    def _request(**kwargs):
        return AssistantRequest(name="Test", description="Test", system_prompt="Test", llm_model_type="gpt-4", **kwargs)

    def test_set_from_none(self, field):
        assistant = self._assistant()
        assistant._map_assistant_request(self._request(**{field: 0.5}))
        assert getattr(assistant, field) == 0.5

    def test_update_existing_value(self, field):
        assistant = self._assistant(**{field: 0.7})
        assistant._map_assistant_request(self._request(**{field: 0.2}))
        assert getattr(assistant, field) == 0.2

    def test_explicit_null_clears_value(self, field):
        assistant = self._assistant(**{field: 0.7})
        assistant._map_assistant_request(self._request(**{field: None}))
        assert getattr(assistant, field) is None

    def test_omitted_preserves_value(self, field):
        assistant = self._assistant(**{field: 0.7})
        assistant._map_assistant_request(self._request())
        assert getattr(assistant, field) == 0.7
