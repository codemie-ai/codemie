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
Test suite for enterprise Langfuse workflow integration.
"""

from unittest.mock import MagicMock, patch


class TestCreateWorkflowTraceContext:
    """Test cases for create_workflow_trace_context function."""

    @patch('codemie.enterprise.langfuse.workflows.is_langfuse_enabled')
    def test_returns_none_when_langfuse_disabled(self, mock_is_enabled):
        """Verify function returns None when Langfuse is disabled."""
        from codemie.enterprise.langfuse.workflows import create_workflow_trace_context

        mock_is_enabled.return_value = False

        result = create_workflow_trace_context(
            execution_id='exec-123',
            workflow_id='wf-456',
            workflow_name='test-workflow',
            user_id='user-789',
            session_id='session-abc',
        )

        assert result is None

    @patch('codemie.enterprise.LangfuseContextManager')
    @patch('codemie.enterprise.build_workflow_metadata')
    @patch('codemie.enterprise.langfuse.workflows.is_langfuse_enabled')
    def test_uses_provided_session_id(self, mock_is_enabled, mock_build_metadata, mock_context_manager):
        """Verify session_id is used when provided."""
        from codemie.enterprise.langfuse.workflows import create_workflow_trace_context

        mock_is_enabled.return_value = True
        mock_build_metadata.return_value = {'langfuse_tags': ['tag1']}
        mock_trace_context = MagicMock()
        mock_context_manager.create_workflow_trace_context.return_value = mock_trace_context

        result = create_workflow_trace_context(
            execution_id='exec-123',
            workflow_id='wf-456',
            workflow_name='test-workflow',
            user_id='user-789',
            session_id='custom-session-123',
        )

        # Verify build_workflow_metadata called with session_id (so langfuse_session_id is correct in metadata)
        mock_build_metadata.assert_called_once()
        build_kwargs = mock_build_metadata.call_args[1]
        assert build_kwargs['session_id'] == 'custom-session-123'

        # Verify LangfuseContextManager called with custom session_id
        mock_context_manager.create_workflow_trace_context.assert_called_once()
        call_kwargs = mock_context_manager.create_workflow_trace_context.call_args[1]
        assert call_kwargs['session_id'] == 'custom-session-123'
        assert call_kwargs['execution_id'] == 'exec-123'
        assert result == mock_trace_context

    @patch('codemie.enterprise.LangfuseContextManager')
    @patch('codemie.enterprise.build_workflow_metadata')
    @patch('codemie.enterprise.langfuse.workflows.is_langfuse_enabled')
    def test_falls_back_to_execution_id_when_session_id_none(
        self, mock_is_enabled, mock_build_metadata, mock_context_manager
    ):
        """Verify execution_id is used as fallback when session_id is None."""
        from codemie.enterprise.langfuse.workflows import create_workflow_trace_context

        mock_is_enabled.return_value = True
        mock_build_metadata.return_value = {'langfuse_tags': ['tag1']}
        mock_trace_context = MagicMock()
        mock_context_manager.create_workflow_trace_context.return_value = mock_trace_context

        result = create_workflow_trace_context(
            execution_id='exec-123',
            workflow_id='wf-456',
            workflow_name='test-workflow',
            user_id='user-789',
            session_id=None,  # Not provided
        )

        # Verify build_workflow_metadata called with execution_id as fallback session_id
        mock_build_metadata.assert_called_once()
        build_kwargs = mock_build_metadata.call_args[1]
        assert build_kwargs['session_id'] == 'exec-123'  # Falls back to execution_id

        # Verify LangfuseContextManager called with execution_id as fallback
        mock_context_manager.create_workflow_trace_context.assert_called_once()
        call_kwargs = mock_context_manager.create_workflow_trace_context.call_args[1]
        assert call_kwargs['session_id'] == 'exec-123'  # Falls back to execution_id
        assert call_kwargs['execution_id'] == 'exec-123'
        assert result == mock_trace_context

    @patch('codemie.enterprise.LangfuseContextManager')
    @patch('codemie.enterprise.build_workflow_metadata')
    @patch('codemie.enterprise.langfuse.workflows.is_langfuse_enabled')
    def test_falls_back_when_session_id_not_provided(self, mock_is_enabled, mock_build_metadata, mock_context_manager):
        """Verify backward compatibility when session_id parameter not provided."""
        from codemie.enterprise.langfuse.workflows import create_workflow_trace_context

        mock_is_enabled.return_value = True
        mock_build_metadata.return_value = {'langfuse_tags': ['tag1']}
        mock_trace_context = MagicMock()
        mock_context_manager.create_workflow_trace_context.return_value = mock_trace_context

        # Call without session_id parameter (backward compatibility)
        result = create_workflow_trace_context(
            execution_id='exec-123',
            workflow_id='wf-456',
            workflow_name='test-workflow',
            user_id='user-789',
            # session_id not provided at all
        )

        # Verify LangfuseContextManager called with execution_id as fallback
        mock_context_manager.create_workflow_trace_context.assert_called_once()
        call_kwargs = mock_context_manager.create_workflow_trace_context.call_args[1]
        assert call_kwargs['session_id'] == 'exec-123'  # Falls back to execution_id
        assert result == mock_trace_context

    @patch('codemie.enterprise.LangfuseContextManager')
    @patch('codemie.enterprise.build_workflow_metadata')
    @patch('codemie.enterprise.langfuse.workflows.is_langfuse_enabled')
    @patch('codemie.configs.logger')
    def test_logs_session_id_correctly(self, mock_logger, mock_is_enabled, mock_build_metadata, mock_context_manager):
        """Verify logging includes both execution_id and session_id."""
        from codemie.enterprise.langfuse.workflows import create_workflow_trace_context

        mock_is_enabled.return_value = True
        mock_build_metadata.return_value = {'langfuse_tags': []}
        mock_trace_context = MagicMock()
        mock_context_manager.create_workflow_trace_context.return_value = mock_trace_context

        # Test with custom session_id
        create_workflow_trace_context(
            execution_id='exec-123',
            workflow_id='wf-456',
            workflow_name='test-workflow',
            user_id='user-789',
            session_id='custom-session-456',
        )

        # Verify logging includes both IDs
        mock_logger.info.assert_called_once()
        log_message = mock_logger.info.call_args[0][0]
        assert 'execution_id=exec-123' in log_message
        assert 'session_id=custom-session-456' in log_message
        assert 'workflow=test-workflow' in log_message

    @patch('codemie.enterprise.LangfuseContextManager')
    @patch('codemie.enterprise.build_workflow_metadata')
    @patch('codemie.enterprise.langfuse.workflows.is_langfuse_enabled')
    def test_handles_exception_gracefully(self, mock_is_enabled, mock_build_metadata, mock_context_manager):
        """Verify function returns None on exception and logs warning."""
        from codemie.enterprise.langfuse.workflows import create_workflow_trace_context

        mock_is_enabled.return_value = True
        mock_build_metadata.side_effect = Exception("Test error")

        with patch('codemie.configs.logger') as mock_logger:
            result = create_workflow_trace_context(
                execution_id='exec-123',
                workflow_id='wf-456',
                workflow_name='test-workflow',
                user_id='user-789',
                session_id='session-abc',
            )

            assert result is None
            mock_logger.warning.assert_called_once()
            assert 'Failed to create workflow trace context' in mock_logger.warning.call_args[0][0]

    @patch('codemie.enterprise.LangfuseContextManager')
    @patch('codemie.enterprise.build_workflow_metadata')
    @patch('codemie.enterprise.langfuse.workflows.is_langfuse_enabled')
    def test_empty_string_session_id_is_used(self, mock_is_enabled, mock_build_metadata, mock_context_manager):
        """Verify empty string session_id is passed through (edge case)."""
        from codemie.enterprise.langfuse.workflows import create_workflow_trace_context

        mock_is_enabled.return_value = True
        mock_build_metadata.return_value = {'langfuse_tags': []}
        mock_trace_context = MagicMock()
        mock_context_manager.create_workflow_trace_context.return_value = mock_trace_context

        result = create_workflow_trace_context(
            execution_id='exec-123',
            workflow_id='wf-456',
            workflow_name='test-workflow',
            user_id='user-789',
            session_id='',  # Empty string
        )

        # Empty string is a valid value, should be used not replaced with execution_id
        mock_context_manager.create_workflow_trace_context.assert_called_once()
        call_kwargs = mock_context_manager.create_workflow_trace_context.call_args[1]
        # Note: Empty string is falsy but not None, so it should be used
        # The current implementation: session_id if session_id is not None else execution_id
        assert call_kwargs['session_id'] == ''  # Empty string used as-is
        assert result == mock_trace_context

    @patch('codemie.enterprise.LangfuseContextManager')
    @patch('codemie.enterprise.build_workflow_metadata')
    @patch('codemie.enterprise.langfuse.workflows.is_langfuse_enabled')
    def test_tags_passed_to_build_metadata_and_context_manager(
        self, mock_is_enabled, mock_build_metadata, mock_context_manager
    ):
        """Verify tags are passed to build_workflow_metadata and LangfuseContextManager."""
        from codemie.enterprise.langfuse.workflows import create_workflow_trace_context

        mock_is_enabled.return_value = True
        mock_build_metadata.return_value = {'langfuse_tags': ['tag1', 'tag2']}
        mock_trace_context = MagicMock()
        mock_context_manager.create_workflow_trace_context.return_value = mock_trace_context

        result = create_workflow_trace_context(
            execution_id='exec-123',
            workflow_id='wf-456',
            workflow_name='test-workflow',
            user_id='user-789',
            session_id='session-abc',
            tags=['tag1', 'tag2'],
        )

        # Verify build_workflow_metadata received the tags
        mock_build_metadata.assert_called_once()
        build_kwargs = mock_build_metadata.call_args[1]
        assert build_kwargs['tags'] == ['tag1', 'tag2']

        # Verify LangfuseContextManager received the tags from metadata
        mock_context_manager.create_workflow_trace_context.assert_called_once()
        call_kwargs = mock_context_manager.create_workflow_trace_context.call_args[1]
        assert call_kwargs['tags'] == ['tag1', 'tag2']
        assert result == mock_trace_context
