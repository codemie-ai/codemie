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
Test suite for CreateWorkflowExecutionRequest with propagate_headers field.

Tests the propagate_headers field in workflow execution request models including
default values and backward compatibility.
"""

import pytest
from pydantic import ValidationError

from codemie.core.workflow_models.workflow_execution import CreateWorkflowExecutionRequest


class TestCreateWorkflowExecutionRequestPropagateHeaders:
    """Test cases for CreateWorkflowExecutionRequest propagate_headers field."""

    def test_propagate_headers_true(self):
        """
        TC-3.1.1: Verify propagate_headers field works.

        Priority: Critical
        """
        # Arrange & Act
        request = CreateWorkflowExecutionRequest(user_input='test input', file_name='test.txt', propagate_headers=True)

        # Assert - field is set correctly
        assert request.propagate_headers is True

        # Assert - serialization works
        request_dict = request.model_dump()
        assert 'propagate_headers' in request_dict
        assert request_dict['propagate_headers'] is True

    def test_propagate_headers_default_false(self):
        """
        TC-3.1.2: Verify default is False.

        Priority: High
        """
        # Arrange & Act - create without propagate_headers
        request = CreateWorkflowExecutionRequest(user_input='test input')

        # Assert - default is False (backward compatible)
        assert request.propagate_headers is False

    def test_propagate_headers_false_explicit(self):
        """
        Verify explicit False value works.

        Priority: High
        """
        # Arrange & Act
        request = CreateWorkflowExecutionRequest(user_input='test input', propagate_headers=False)

        # Assert
        assert request.propagate_headers is False

    def test_propagate_headers_with_all_fields(self):
        """
        Verify propagate_headers works with all fields.

        Priority: High
        """
        # Arrange & Act
        request = CreateWorkflowExecutionRequest(user_input='test input', file_name='test.txt', propagate_headers=True)

        # Assert - all fields accessible
        assert request.user_input == 'test input'
        assert request.file_name == 'test.txt'
        assert request.propagate_headers is True

    def test_propagate_headers_serialization(self):
        """
        Verify serialization includes propagate_headers.

        Priority: High
        """
        # Arrange
        request = CreateWorkflowExecutionRequest(user_input='test input', propagate_headers=True)

        # Act
        request_dict = request.model_dump()

        # Assert
        assert 'propagate_headers' in request_dict
        assert request_dict['propagate_headers'] is True

    def test_propagate_headers_backward_compatibility(self):
        """
        Verify existing requests without propagate_headers still work.

        Priority: Critical
        """
        # Arrange - old request without propagate_headers
        old_request_data = {
            'user_input': 'test input',
            'file_name': 'test.txt',
            # No propagate_headers field
        }

        # Act
        request = CreateWorkflowExecutionRequest(**old_request_data)

        # Assert - defaults to False for backward compatibility
        assert request.propagate_headers is False
        assert request.user_input == 'test input'

    def test_propagate_headers_invalid_type(self):
        """
        Verify Pydantic validation for invalid types.

        Priority: High
        """
        # Arrange & Act & Assert
        with pytest.raises(ValidationError) as exc_info:
            CreateWorkflowExecutionRequest(user_input='test input', propagate_headers='invalid-string')

        # Verify validation error
        assert 'propagate_headers' in str(exc_info.value).lower()

    def test_propagate_headers_coercion_from_string(self):
        """
        Verify Pydantic coerces string to boolean.

        Priority: High
        """
        # Arrange
        request_data = {'user_input': 'test input', 'propagate_headers': 'true'}

        # Act
        request = CreateWorkflowExecutionRequest(**request_data)

        # Assert - coerced to boolean
        assert request.propagate_headers is True
        assert isinstance(request.propagate_headers, bool)
