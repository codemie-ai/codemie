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
Test suite for CreateWorkflowExecutionRequest session_id field.
"""

from codemie.core.workflow_models.workflow_execution import CreateWorkflowExecutionRequest


class TestCreateWorkflowExecutionRequestSessionId:
    """Test cases for CreateWorkflowExecutionRequest session_id field."""

    def test_session_id_provided(self):
        """Verify session_id field accepts string value."""
        request = CreateWorkflowExecutionRequest(user_input='test', session_id='custom-session-123')
        assert request.session_id == 'custom-session-123'

    def test_session_id_default_none(self):
        """Verify session_id defaults to None (backward compatibility)."""
        request = CreateWorkflowExecutionRequest(user_input='test')
        assert request.session_id is None

    def test_session_id_explicit_none(self):
        """Verify explicit None value works."""
        request = CreateWorkflowExecutionRequest(user_input='test', session_id=None)
        assert request.session_id is None

    def test_session_id_serialization(self):
        """Verify serialization includes session_id."""
        request = CreateWorkflowExecutionRequest(user_input='test', session_id='test-session')
        request_dict = request.model_dump()
        assert 'session_id' in request_dict
        assert request_dict['session_id'] == 'test-session'

    def test_session_id_serialization_with_none(self):
        """Verify serialization handles None session_id."""
        request = CreateWorkflowExecutionRequest(user_input='test', session_id=None)
        request_dict = request.model_dump()
        assert 'session_id' in request_dict
        assert request_dict['session_id'] is None

    def test_session_id_backward_compatibility(self):
        """Verify existing requests without session_id still work."""
        old_request_data = {
            'user_input': 'test input',
            'file_name': 'test.txt',
            'propagate_headers': False,
            'stream': False,
            # No session_id field - simulates old API calls
        }
        request = CreateWorkflowExecutionRequest(**old_request_data)
        assert request.session_id is None
        assert request.user_input == 'test input'
        assert request.file_name == 'test.txt'

    def test_session_id_with_conversation_id(self):
        """Verify session_id works alongside conversation_id."""
        request = CreateWorkflowExecutionRequest(
            user_input='test', conversation_id='conv-123', session_id='session-456'
        )
        assert request.conversation_id == 'conv-123'
        assert request.session_id == 'session-456'

    def test_session_id_with_all_fields(self):
        """Verify session_id works with all other fields populated."""
        request = CreateWorkflowExecutionRequest(
            user_input='test input',
            file_name='test.txt',
            propagate_headers=True,
            stream=True,
            conversation_id='conv-123',
            session_id='session-456',
            disable_cache=True,
        )
        assert request.user_input == 'test input'
        assert request.file_name == 'test.txt'
        assert request.propagate_headers is True
        assert request.stream is True
        assert request.conversation_id == 'conv-123'
        assert request.session_id == 'session-456'
        assert request.disable_cache is True

    def test_session_id_empty_string(self):
        """Verify empty string session_id is accepted (edge case)."""
        # Note: Empty strings are technically valid but not recommended
        # The system should handle this gracefully
        request = CreateWorkflowExecutionRequest(user_input='test', session_id='')
        assert request.session_id == ''

    def test_session_id_with_special_characters(self):
        """Verify session_id accepts special characters."""
        special_session_id = 'session-123_test.conv@user#2024'
        request = CreateWorkflowExecutionRequest(user_input='test', session_id=special_session_id)
        assert request.session_id == special_session_id

    def test_session_id_long_value(self):
        """Verify session_id accepts reasonably long strings."""
        long_session_id = 'session-' + 'x' * 200  # 208 characters total
        request = CreateWorkflowExecutionRequest(user_input='test', session_id=long_session_id)
        assert request.session_id == long_session_id

    def test_session_id_json_deserialization(self):
        """Verify session_id can be deserialized from JSON."""
        json_data = {'user_input': 'test input', 'session_id': 'json-session-123'}
        request = CreateWorkflowExecutionRequest(**json_data)
        assert request.session_id == 'json-session-123'

    def test_session_id_model_validation(self):
        """Verify Pydantic model validation for session_id."""
        # This should not raise any validation errors
        request = CreateWorkflowExecutionRequest(user_input='test', session_id='valid-session')
        # Validate the model
        validated = CreateWorkflowExecutionRequest.model_validate(request.model_dump())
        assert validated.session_id == 'valid-session'
