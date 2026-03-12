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
Tests for workflow_execution_id field in core models.

This module contains tests for the workflow_execution_id field added to
AssistantChatRequest and its integration with the execution context system.
"""

import pytest
from pydantic import ValidationError

from codemie.core.models import AssistantChatRequest, ChatMessage
from codemie.core.constants import ChatRole


class TestAssistantChatRequestWorkflowExecutionId:
    """Tests for workflow_execution_id field in AssistantChatRequest."""

    def test_basic_instantiation_without_workflow_execution_id(self):
        """Test that AssistantChatRequest can be created without workflow_execution_id."""
        request = AssistantChatRequest(
            text="Test request",
            history=[],
        )

        assert request.text == "Test request"
        assert request.workflow_execution_id is None

    def test_instantiation_with_workflow_execution_id(self):
        """Test that AssistantChatRequest can be created with workflow_execution_id."""
        request = AssistantChatRequest(
            text="Test request with workflow",
            history=[],
            workflow_execution_id="workflow-123-456",
        )

        assert request.text == "Test request with workflow"
        assert request.workflow_execution_id == "workflow-123-456"

    def test_workflow_execution_id_accepts_none(self):
        """Test that workflow_execution_id accepts None value."""
        request = AssistantChatRequest(
            text="Test request",
            history=[],
            workflow_execution_id=None,
        )

        assert request.workflow_execution_id is None

    def test_workflow_execution_id_accepts_empty_string(self):
        """Test that workflow_execution_id accepts empty string."""
        request = AssistantChatRequest(
            text="Test request",
            history=[],
            workflow_execution_id="",
        )

        assert request.workflow_execution_id == ""

    def test_workflow_execution_id_accepts_various_formats(self):
        """Test that workflow_execution_id accepts various string formats."""
        test_cases = [
            "simple-id",
            "workflow_123",
            "uuid-4f8b4c2e-8d5e-4a3c-9f1e-2b7d8c5a9e1f",
            "complex.workflow-id_with.special-chars",
            "123456789",
            "workflow execution id with spaces",
        ]

        for workflow_id in test_cases:
            request = AssistantChatRequest(
                text="Test request",
                history=[],
                workflow_execution_id=workflow_id,
            )
            assert request.workflow_execution_id == workflow_id

    def test_serialization_deserialization_with_workflow_execution_id(self):
        """Test JSON serialization and deserialization with workflow_execution_id."""
        original = AssistantChatRequest(
            text="Test serialization request",
            history=[
                ChatMessage(role=ChatRole.USER, message="Previous message"),
                ChatMessage(role=ChatRole.ASSISTANT, message="Previous response"),
            ],
            history_index=1,
            mcp_server_single_usage=True,
            workflow_execution_id="serialization-test-123",
            stream=False,
        )

        # Serialize to JSON
        json_str = original.model_dump_json()

        # Deserialize from JSON
        import json

        deserialized = AssistantChatRequest.model_validate(json.loads(json_str))

        # Verify all fields including workflow_execution_id
        assert deserialized.text == original.text
        assert len(deserialized.history) == len(original.history)
        assert deserialized.history_index == original.history_index
        assert deserialized.mcp_server_single_usage == original.mcp_server_single_usage
        assert deserialized.workflow_execution_id == original.workflow_execution_id
        assert deserialized.stream == original.stream

    def test_serialization_without_workflow_execution_id(self):
        """Test JSON serialization and deserialization without workflow_execution_id."""
        original = AssistantChatRequest(
            text="Test request without workflow",
            history=[],
        )

        # Serialize to JSON
        json_str = original.model_dump_json()

        # Deserialize from JSON
        import json

        data = json.loads(json_str)

        # Verify workflow_execution_id is included as null
        assert "workflow_execution_id" in data
        assert data["workflow_execution_id"] is None

        deserialized = AssistantChatRequest.model_validate(data)
        assert deserialized.workflow_execution_id is None

    def test_model_dump_includes_workflow_execution_id(self):
        """Test that model_dump includes workflow_execution_id field."""
        request = AssistantChatRequest(
            text="Test model dump",
            history=[],
            workflow_execution_id="dump-test-456",
        )

        dumped = request.model_dump()

        assert "workflow_execution_id" in dumped
        assert dumped["workflow_execution_id"] == "dump-test-456"

    def test_model_dump_excludes_none_workflow_execution_id_when_exclude_none(self):
        """Test that model_dump can exclude None workflow_execution_id."""
        request = AssistantChatRequest(
            text="Test exclude none",
            history=[],
            workflow_execution_id=None,
        )

        dumped = request.model_dump(exclude_none=True)

        # When exclude_none=True, None fields should be excluded
        assert "workflow_execution_id" not in dumped

    def test_workflow_execution_id_field_metadata(self):
        """Test that workflow_execution_id field has correct metadata."""
        # Get field info from the model
        field_info = AssistantChatRequest.model_fields.get("workflow_execution_id")

        assert field_info is not None
        assert field_info.default is None
        assert field_info.description == "Identifier for the workflow execution"

    def test_integration_with_other_fields(self):
        """Test workflow_execution_id integration with other request fields."""
        request = AssistantChatRequest(
            text="Integration test request",
            history=[
                ChatMessage(role=ChatRole.USER, message="User message"),
                ChatMessage(role=ChatRole.ASSISTANT, message="Assistant response"),
            ],
            history_index=0,
            mcp_server_single_usage=False,
            workflow_execution_id="integration-test-789",
            stream=True,
        )

        # Verify all fields are set correctly
        assert request.text == "Integration test request"
        assert len(request.history) == 2
        assert request.history_index == 0
        assert request.mcp_server_single_usage is False
        assert request.workflow_execution_id == "integration-test-789"
        assert request.stream is True

    def test_workflow_execution_id_does_not_affect_validation(self):
        """Test that workflow_execution_id doesn't interfere with other field validation."""
        # Test with valid required fields and workflow_execution_id
        valid_request = AssistantChatRequest(
            text="Valid request",
            workflow_execution_id="validation-test",
        )
        assert valid_request.text == "Valid request"
        assert valid_request.workflow_execution_id == "validation-test"

        # Test that invalid field types still cause validation errors
        with pytest.raises(ValidationError):
            AssistantChatRequest(
                text="Valid request",
                workflow_execution_id="validation-test",
                history_index="invalid_int",  # This should be an int, not a string
            )

    def test_workflow_execution_id_type_validation(self):
        """Test that workflow_execution_id validates string type correctly."""
        # Valid string values should work
        request = AssistantChatRequest(
            text="Type validation test",
            workflow_execution_id="string-value",
        )
        assert request.workflow_execution_id == "string-value"

        # None should work (it's Optional)
        request_none = AssistantChatRequest(
            text="Type validation test",
            workflow_execution_id=None,
        )
        assert request_none.workflow_execution_id is None

        # Invalid types should raise ValidationError
        with pytest.raises(ValidationError):
            AssistantChatRequest(
                text="Type validation test",
                workflow_execution_id=123,  # Integer instead of string
            )

        with pytest.raises(ValidationError):
            AssistantChatRequest(
                text="Type validation test",
                workflow_execution_id=["list", "instead", "of", "string"],  # List instead of string
            )

    def test_workflow_execution_id_in_model_fields(self):
        """Test that workflow_execution_id is properly registered in model fields."""
        model_fields = AssistantChatRequest.model_fields

        assert "workflow_execution_id" in model_fields

        field = model_fields["workflow_execution_id"]
        # Check that it's optional (has default None)
        assert field.default is None
        # Check annotation allows None
        assert hasattr(field, 'annotation')
