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
Test suite for MCPExecutionContext model with request_headers support.

Tests the request_headers field addition to MCPExecutionContext including
serialization, deserialization, and handling of None/empty values.
"""

import pytest
from pydantic import ValidationError

from codemie.service.mcp.models import MCPExecutionContext


class TestMCPExecutionContextWithHeaders:
    """Test cases for MCPExecutionContext request_headers field."""

    def test_execution_context_with_request_headers(self):
        """
        TC-1.2.1: Verify MCPExecutionContext correctly stores and serializes request_headers.

        Priority: Critical
        """
        # Arrange
        test_headers = {'X-Tenant-ID': 'tenant-123', 'X-User-ID': 'user-456', 'X-Correlation-ID': 'corr-789'}

        # Act
        context = MCPExecutionContext(
            user_id='user-123',
            assistant_id='asst-456',
            project_name='test-project',
            workflow_execution_id='wf-789',
            request_headers=test_headers,
        )

        # Assert - field is stored correctly
        assert context.request_headers is not None
        assert context.request_headers == test_headers
        assert context.request_headers['X-Tenant-ID'] == 'tenant-123'
        assert context.request_headers['X-User-ID'] == 'user-456'

        # Assert - serialization works
        context_dict = context.model_dump()
        assert 'request_headers' in context_dict
        assert context_dict['request_headers'] == test_headers

    def test_execution_context_with_none_request_headers(self):
        """
        TC-1.2.2: Verify MCPExecutionContext handles None request_headers gracefully.

        Priority: High
        """
        # Arrange & Act
        context = MCPExecutionContext(
            user_id='user-123', assistant_id='asst-456', project_name='test-project', request_headers=None
        )

        # Assert - None is handled gracefully
        assert context.request_headers is None

        # Assert - serialization works with None
        context_dict = context.model_dump()
        assert 'request_headers' in context_dict
        assert context_dict['request_headers'] is None

        # Assert - no errors occur when accessing
        headers = context.request_headers
        assert headers is None

    def test_execution_context_with_empty_dict_headers(self):
        """
        TC-1.2.3: Verify empty dictionary is handled correctly.

        Priority: Medium (included for completeness)
        """
        # Arrange & Act
        context = MCPExecutionContext(user_id='user-123', request_headers={})

        # Assert
        assert context.request_headers == {}
        assert isinstance(context.request_headers, dict)
        assert len(context.request_headers) == 0

        # Assert - serialization works
        context_dict = context.model_dump()
        assert context_dict['request_headers'] == {}

    def test_execution_context_default_headers_is_none(self):
        """
        TC-1.2.2: Verify default value for request_headers is None.

        Priority: High
        """
        # Arrange & Act - create context without specifying request_headers
        context = MCPExecutionContext(user_id='user-123', assistant_id='asst-456')

        # Assert - default should be None
        assert context.request_headers is None

    def test_execution_context_headers_with_special_characters(self):
        """
        Verify headers with special characters are preserved.

        Priority: High
        """
        # Arrange
        test_headers = {'X-Custom-Data': 'value=test&foo=bar;key=123', 'X-Token': 'Bearer abc123-def456_ghi789'}

        # Act
        context = MCPExecutionContext(user_id='user-123', request_headers=test_headers)

        # Assert - special characters preserved
        assert context.request_headers['X-Custom-Data'] == 'value=test&foo=bar;key=123'
        assert context.request_headers['X-Token'] == 'Bearer abc123-def456_ghi789'

    def test_execution_context_serialization_round_trip(self):
        """
        Verify round-trip serialization/deserialization with headers.

        Priority: High
        """
        # Arrange
        original_context = MCPExecutionContext(
            user_id='user-123',
            assistant_id='asst-456',
            project_name='test-project',
            workflow_execution_id='wf-789',
            request_headers={'X-Tenant-ID': 'tenant-123', 'X-User-ID': 'user-456'},
        )

        # Act - serialize and deserialize
        context_dict = original_context.model_dump()
        restored_context = MCPExecutionContext(**context_dict)

        # Assert - all fields preserved
        assert restored_context.user_id == original_context.user_id
        assert restored_context.assistant_id == original_context.assistant_id
        assert restored_context.project_name == original_context.project_name
        assert restored_context.workflow_execution_id == original_context.workflow_execution_id
        assert restored_context.request_headers == original_context.request_headers

    def test_execution_context_invalid_headers_type(self):
        """
        Verify Pydantic validation for invalid headers type.

        Priority: High
        """
        # Arrange & Act & Assert
        with pytest.raises(ValidationError) as exc_info:
            MCPExecutionContext(
                user_id='user-123',
                request_headers='invalid-string-not-dict',  # Should be dict, not string
            )

        # Verify validation error mentions request_headers
        assert 'request_headers' in str(exc_info.value).lower()

    def test_execution_context_headers_with_unicode(self):
        """
        Verify headers with Unicode characters are handled.

        Priority: High
        """
        # Arrange
        test_headers = {'X-User-Name': 'José García', 'X-Location': 'São Paulo'}

        # Act
        context = MCPExecutionContext(user_id='user-123', request_headers=test_headers)

        # Assert - Unicode preserved
        assert context.request_headers['X-User-Name'] == 'José García'
        assert context.request_headers['X-Location'] == 'São Paulo'

    def test_execution_context_all_fields_with_headers(self):
        """
        Verify all fields work together with request_headers.

        Priority: High
        """
        # Arrange & Act
        context = MCPExecutionContext(
            user_id='user-123',
            assistant_id='asst-456',
            project_name='test-project',
            workflow_execution_id='wf-789',
            request_headers={'X-Custom': 'value'},
        )

        # Assert - all fields accessible
        assert context.user_id == 'user-123'
        assert context.assistant_id == 'asst-456'
        assert context.project_name == 'test-project'
        assert context.workflow_execution_id == 'wf-789'
        assert context.request_headers == {'X-Custom': 'value'}

        # Assert - model validation passed
        assert isinstance(context, MCPExecutionContext)
