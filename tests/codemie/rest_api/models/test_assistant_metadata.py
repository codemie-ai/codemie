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

"""Tests for AssistantConfiguration custom_metadata field"""

from sqlalchemy.dialects.postgresql import JSONB

from codemie.rest_api.models.assistant import AssistantConfiguration, AssistantRequest


class TestAssistantConfigurationMetadata:
    """Test suite for custom_metadata field in AssistantConfiguration model."""

    def test_metadata_field_exists(self):
        """Test that custom_metadata field exists on AssistantConfiguration."""
        assert hasattr(AssistantConfiguration, 'custom_metadata')

    def test_metadata_field_nullable(self):
        """Test that custom_metadata field is nullable with default None."""
        field = AssistantConfiguration.model_fields['custom_metadata']
        assert field.default is None
        assert not field.is_required()

    def test_metadata_column_type(self):
        """Test that custom_metadata maps to JSONB column named 'custom_metadata'."""
        # Access the SQLAlchemy column
        table = AssistantConfiguration.__table__
        custom_metadata_column = table.c.custom_metadata

        assert custom_metadata_column is not None
        assert isinstance(custom_metadata_column.type, JSONB)
        assert custom_metadata_column.nullable is True

    def test_metadata_with_object(self):
        """Test creating AssistantConfiguration with object metadata."""
        metadata = {"workflow_state": "pending_approval", "external_id": "ext-12345"}

        config = AssistantConfiguration(
            id="test-id",
            assistant_id="assistant-1",
            version_number=1,
            description="Test assistant",
            system_prompt="Test prompt",
            custom_metadata=metadata,
        )

        assert config.custom_metadata == metadata
        assert config.custom_metadata["workflow_state"] == "pending_approval"

    def test_metadata_with_nested_object(self):
        """Test creating AssistantConfiguration with nested object metadata."""
        metadata = {"custom_flags": {"reviewed": True, "priority": 3, "tags": ["production", "customer-facing"]}}

        config = AssistantConfiguration(
            id="test-id",
            assistant_id="assistant-1",
            version_number=1,
            description="Test assistant",
            system_prompt="Test prompt",
            custom_metadata=metadata,
        )

        assert config.custom_metadata["custom_flags"]["reviewed"] is True
        assert config.custom_metadata["custom_flags"]["priority"] == 3
        assert len(config.custom_metadata["custom_flags"]["tags"]) == 2

    def test_metadata_with_array(self):
        """Test creating AssistantConfiguration with array metadata."""
        metadata = {"tags": ["tag1", "tag2", "tag3"]}

        config = AssistantConfiguration(
            id="test-id",
            assistant_id="assistant-1",
            version_number=1,
            description="Test assistant",
            system_prompt="Test prompt",
            custom_metadata=metadata,
        )

        assert isinstance(config.custom_metadata["tags"], list)
        assert len(config.custom_metadata["tags"]) == 3

    def test_metadata_with_primitives(self):
        """Test creating AssistantConfiguration with primitive type metadata."""
        metadata = {
            "string_value": "test",
            "number_value": 42,
            "float_value": 3.14,
            "boolean_value": True,
            "null_value": None,
        }

        config = AssistantConfiguration(
            id="test-id",
            assistant_id="assistant-1",
            version_number=1,
            description="Test assistant",
            system_prompt="Test prompt",
            custom_metadata=metadata,
        )

        assert config.custom_metadata["string_value"] == "test"
        assert config.custom_metadata["number_value"] == 42
        assert config.custom_metadata["float_value"] == 3.14
        assert config.custom_metadata["boolean_value"] is True
        assert config.custom_metadata["null_value"] is None

    def test_metadata_with_null(self):
        """Test creating AssistantConfiguration with NULL metadata."""
        config = AssistantConfiguration(
            id="test-id",
            assistant_id="assistant-1",
            version_number=1,
            description="Test assistant",
            system_prompt="Test prompt",
            custom_metadata=None,
        )

        assert config.custom_metadata is None

    def test_metadata_with_empty_object(self):
        """Test creating AssistantConfiguration with empty object metadata."""
        config = AssistantConfiguration(
            id="test-id",
            assistant_id="assistant-1",
            version_number=1,
            description="Test assistant",
            system_prompt="Test prompt",
            custom_metadata={},
        )

        assert config.custom_metadata == {}

    def test_metadata_model_dump_includes_field(self):
        """Test that model_dump() includes custom_metadata field."""
        metadata = {"key": "value"}

        config = AssistantConfiguration(
            id="test-id",
            assistant_id="assistant-1",
            version_number=1,
            description="Test assistant",
            system_prompt="Test prompt",
            custom_metadata=metadata,
        )

        dumped = config.model_dump()
        assert "custom_metadata" in dumped
        assert dumped["custom_metadata"] == metadata

    def test_metadata_model_dump_with_null(self):
        """Test that model_dump() handles NULL metadata correctly."""
        config = AssistantConfiguration(
            id="test-id",
            assistant_id="assistant-1",
            version_number=1,
            description="Test assistant",
            system_prompt="Test prompt",
            custom_metadata=None,
        )

        dumped = config.model_dump()
        assert "custom_metadata" in dumped
        assert dumped["custom_metadata"] is None


class TestAssistantRequestMetadata:
    """Test suite for custom_metadata field in AssistantRequest model."""

    def test_metadata_field_exists(self):
        """Test that custom_metadata field exists on AssistantRequest."""
        # Check field exists in model fields
        assert 'custom_metadata' in AssistantRequest.model_fields

    def test_request_with_metadata(self):
        """Test creating AssistantRequest with custom_metadata."""
        request = AssistantRequest(
            name="Test Assistant",
            description="Test description",
            system_prompt="Test prompt",
            llm_model_type="gpt-4",
            custom_metadata={"key": "value"},
        )

        assert request.custom_metadata == {"key": "value"}

    def test_request_with_null_metadata(self):
        """Test creating AssistantRequest with NULL custom_metadata."""
        request = AssistantRequest(
            name="Test Assistant",
            description="Test description",
            system_prompt="Test prompt",
            llm_model_type="gpt-4",
            custom_metadata=None,
        )

        assert request.custom_metadata is None

    def test_request_without_metadata(self):
        """Test creating AssistantRequest without custom_metadata field."""
        request = AssistantRequest(
            name="Test Assistant", description="Test description", system_prompt="Test prompt", llm_model_type="gpt-4"
        )

        # Should default to None
        assert request.custom_metadata is None

    def test_request_model_dump_includes_metadata(self):
        """Test that model_dump() includes custom_metadata field."""
        request = AssistantRequest(
            name="Test Assistant",
            description="Test description",
            system_prompt="Test prompt",
            llm_model_type="gpt-4",
            custom_metadata={"key": "value"},
        )

        dumped = request.model_dump()
        assert "custom_metadata" in dumped
        assert dumped["custom_metadata"] == {"key": "value"}

    def test_request_from_dict(self):
        """Test creating AssistantRequest from dict with custom_metadata."""
        data = {
            "name": "Test Assistant",
            "description": "Test description",
            "system_prompt": "Test prompt",
            "llm_model_type": "gpt-4",
            "custom_metadata": {"external_id": "ext-123"},
        }

        request = AssistantRequest(**data)
        assert request.custom_metadata == {"external_id": "ext-123"}

    def test_request_complex_metadata(self):
        """Test AssistantRequest with complex nested custom_metadata structure."""
        metadata = {
            "workflow": {
                "stage": "development",
                "approvals": ["user1", "user2"],
                "flags": {"requires_testing": True, "priority": 5},
            }
        }

        request = AssistantRequest(
            name="Test Assistant",
            description="Test description",
            system_prompt="Test prompt",
            llm_model_type="gpt-4",
            custom_metadata=metadata,
        )

        assert request.custom_metadata["workflow"]["stage"] == "development"
        assert len(request.custom_metadata["workflow"]["approvals"]) == 2
        assert request.custom_metadata["workflow"]["flags"]["requires_testing"] is True
