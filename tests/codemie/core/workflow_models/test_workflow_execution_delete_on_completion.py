# Copyright 2026 EPAM Systems, Inc. ("EPAM")
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
Test suite for CreateWorkflowExecutionRequest delete_on_completion field.

Tests the delete_on_completion field including default values,
explicit values, serialization, and backward compatibility.
"""

import pytest
from pydantic import ValidationError

from codemie.core.workflow_models.workflow_execution import CreateWorkflowExecutionRequest


class TestCreateWorkflowExecutionRequestDeleteOnCompletion:
    """Test cases for CreateWorkflowExecutionRequest delete_on_completion field."""

    def test_delete_on_completion_default_false(self):
        """Verify default is False for backward compatibility."""
        request = CreateWorkflowExecutionRequest(user_input='test input')

        assert request.delete_on_completion is False

    def test_delete_on_completion_explicit_true(self):
        """Verify explicit True value works."""
        request = CreateWorkflowExecutionRequest(user_input='test input', delete_on_completion=True)

        assert request.delete_on_completion is True

    def test_delete_on_completion_explicit_false(self):
        """Verify explicit False value works."""
        request = CreateWorkflowExecutionRequest(user_input='test input', delete_on_completion=False)

        assert request.delete_on_completion is False

    def test_delete_on_completion_serialization(self):
        """Verify serialization includes delete_on_completion."""
        request = CreateWorkflowExecutionRequest(user_input='test input', delete_on_completion=True)

        request_dict = request.model_dump()
        assert 'delete_on_completion' in request_dict
        assert request_dict['delete_on_completion'] is True

    def test_delete_on_completion_backward_compatibility(self):
        """Verify existing requests without delete_on_completion still work."""
        old_request_data = {
            'user_input': 'test input',
            'file_name': 'test.txt',
        }

        request = CreateWorkflowExecutionRequest(**old_request_data)

        assert request.delete_on_completion is False
        assert request.user_input == 'test input'

    def test_delete_on_completion_with_all_fields(self):
        """Verify delete_on_completion works alongside all other fields."""
        request = CreateWorkflowExecutionRequest(
            user_input='test input',
            file_name='test.txt',
            propagate_headers=True,
            stream=True,
            conversation_id='conv-123',
            session_id='session-456',
            disable_cache=True,
            tags=['tag1', 'tag2'],
            delete_on_completion=True,
        )

        assert request.delete_on_completion is True
        assert request.user_input == 'test input'
        assert request.stream is True
        assert request.tags == ['tag1', 'tag2']

    def test_delete_on_completion_invalid_type(self):
        """Verify Pydantic validation for invalid types."""
        with pytest.raises(ValidationError) as exc_info:
            CreateWorkflowExecutionRequest(user_input='test input', delete_on_completion='invalid-string')

        assert 'delete_on_completion' in str(exc_info.value).lower()

    def test_delete_on_completion_coercion_from_string(self):
        """Verify Pydantic coerces string 'true' to boolean."""
        request = CreateWorkflowExecutionRequest(user_input='test input', delete_on_completion='true')

        assert request.delete_on_completion is True
        assert isinstance(request.delete_on_completion, bool)
