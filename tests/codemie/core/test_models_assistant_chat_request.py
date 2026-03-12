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

"""
Test suite for AssistantChatRequest model with propagate_headers field.

Tests the propagate_headers field in AssistantChatRequest model including
default values, validation, and serialization.
"""

import pytest
from pydantic import ValidationError

from codemie.core.models import AssistantChatRequest


class TestAssistantChatRequestPropagateHeaders:
    """Test cases for AssistantChatRequest propagate_headers field."""

    def test_propagate_headers_true(self):
        """
        TC-2.1.1: Verify propagate_headers field works correctly.

        Priority: Critical
        """
        # Arrange & Act
        request = AssistantChatRequest(text='Hello', propagate_headers=True)

        # Assert - field is set correctly
        assert request.propagate_headers is True

        # Assert - Pydantic validation passes
        assert isinstance(request, AssistantChatRequest)

        # Assert - serialization works
        request_dict = request.model_dump()
        assert 'propagate_headers' in request_dict
        assert request_dict['propagate_headers'] is True

    def test_propagate_headers_default_false(self):
        """
        TC-2.1.2: Verify default value is False.

        Priority: High
        """
        # Arrange & Act - create without specifying propagate_headers
        request = AssistantChatRequest(text='Hello')

        # Assert - default is False (backward compatible)
        assert request.propagate_headers is False

    def test_propagate_headers_false_explicit(self):
        """
        Verify explicit False value works correctly.

        Priority: High
        """
        # Arrange & Act
        request = AssistantChatRequest(text='Hello', propagate_headers=False)

        # Assert
        assert request.propagate_headers is False

    def test_propagate_headers_invalid_type(self):
        """
        TC-2.1.3: Verify Pydantic validation for invalid types.

        Priority: Medium (included for completeness)
        """
        # Arrange & Act & Assert
        with pytest.raises(ValidationError) as exc_info:
            AssistantChatRequest(
                text='Hello',
                propagate_headers='invalid-string',  # Should be bool, not string
            )

        # Verify validation error mentions propagate_headers
        assert 'propagate_headers' in str(exc_info.value).lower()

    def test_propagate_headers_with_all_fields(self):
        """
        Verify propagate_headers works with all other fields.

        Priority: High
        """
        # Arrange & Act
        request = AssistantChatRequest(
            conversation_id='conv-123',
            text='Hello',
            content_raw='raw content',
            file_names=['file1.txt', 'file2.pdf'],
            llm_model='gpt-4',
            propagate_headers=True,
        )

        # Assert - all fields accessible
        assert request.conversation_id == 'conv-123'
        assert request.text == 'Hello'
        assert request.propagate_headers is True

    def test_propagate_headers_serialization(self):
        """
        Verify JSON serialization includes propagate_headers.

        Priority: High
        """
        # Arrange
        request = AssistantChatRequest(text='Hello', propagate_headers=True)

        # Act - serialize to dict
        request_dict = request.model_dump()

        # Assert - propagate_headers in output
        assert 'propagate_headers' in request_dict
        assert request_dict['propagate_headers'] is True

    def test_propagate_headers_deserialization(self):
        """
        Verify deserialization from dict works correctly.

        Priority: High
        """
        # Arrange
        request_data = {'text': 'Hello', 'propagate_headers': True}

        # Act
        request = AssistantChatRequest(**request_data)

        # Assert
        assert request.text == 'Hello'
        assert request.propagate_headers is True

    def test_propagate_headers_from_json_true_string(self):
        """
        Verify Pydantic coerces string "true" to boolean True.

        Priority: High
        """
        # Arrange
        request_data = {
            'text': 'Hello',
            'propagate_headers': 'true',  # String instead of bool
        }

        # Act - Pydantic should coerce this
        request = AssistantChatRequest(**request_data)

        # Assert - coerced to boolean True
        assert request.propagate_headers is True
        assert isinstance(request.propagate_headers, bool)

    def test_propagate_headers_from_json_false_string(self):
        """
        Verify Pydantic coerces string "false" to boolean False.

        Priority: High
        """
        # Arrange
        request_data = {
            'text': 'Hello',
            'propagate_headers': 'false',  # String instead of bool
        }

        # Act
        request = AssistantChatRequest(**request_data)

        # Assert - coerced to boolean False
        assert request.propagate_headers is False
        assert isinstance(request.propagate_headers, bool)

    def test_propagate_headers_with_workflow_execution_id(self):
        """
        Verify propagate_headers works with workflow_execution_id.

        Priority: High
        """
        # Arrange & Act
        request = AssistantChatRequest(text='Hello', workflow_execution_id='wf-123', propagate_headers=True)

        # Assert
        assert request.workflow_execution_id == 'wf-123'
        assert request.propagate_headers is True

    def test_propagate_headers_backward_compatibility(self):
        """
        Verify existing code without propagate_headers still works.

        Priority: Critical
        """
        # Arrange - simulate old request without propagate_headers field
        old_request_data = {
            'text': 'Hello',
            'conversation_id': 'conv-123',
            'llm_model': 'gpt-4',
            # No propagate_headers field
        }

        # Act
        request = AssistantChatRequest(**old_request_data)

        # Assert - defaults to False for backward compatibility
        assert request.propagate_headers is False
        assert request.text == 'Hello'
        assert request.llm_model == 'gpt-4'


class TestAssistantChatRequestSaveHistory:
    """Tests for save_history field"""

    def test_save_history_default_true(self):
        """save_history defaults to True"""
        request = AssistantChatRequest(text="Hello")
        assert request.save_history is True

    def test_save_history_explicit_true(self):
        """Can explicitly set save_history=True"""
        request = AssistantChatRequest(text="Hello", save_history=True)
        assert request.save_history is True

    def test_save_history_explicit_false(self):
        """Can set save_history=False"""
        request = AssistantChatRequest(text="Hello", save_history=False)
        assert request.save_history is False

    def test_save_history_invalid_type(self):
        """Invalid type raises ValidationError"""
        with pytest.raises(ValidationError) as exc_info:
            AssistantChatRequest(text="Hello", save_history="invalid")
        assert "save_history" in str(exc_info.value)

    def test_save_history_serialization(self):
        """save_history appears in serialized output"""
        request = AssistantChatRequest(text="Hello", save_history=False)
        data = request.model_dump()
        assert data["save_history"] is False

    def test_save_history_deserialization(self):
        """Can deserialize save_history from dict"""
        data = {"text": "Hello", "save_history": False}
        request = AssistantChatRequest(**data)
        assert request.save_history is False

    def test_save_history_camel_case_alias(self):
        """CamelCase alias works in JSON"""
        json_data = '{"text": "Hello", "saveHistory": false}'
        request = AssistantChatRequest.model_validate_json(json_data)
        assert request.save_history is False

    def test_save_history_backward_compatibility(self):
        """Omitting save_history defaults to True (backward compatible)"""
        data = {"text": "Hello", "stream": False}
        request = AssistantChatRequest(**data)
        assert request.save_history is True
