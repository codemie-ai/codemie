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

import pytest
from unittest.mock import patch, MagicMock

from codemie.configs.logger import logger
from codemie.rest_api.models.assistant import MissingContextException, Assistant, Context, ContextType
from codemie.service.assistant_service import AssistantService


class TestAssistantServiceCheckContext:
    """Tests for the check_context method of AssistantService"""

    def test_check_context_with_missing_context(self):
        """
        Tests that check_context correctly identifies missing contexts and raises a MissingContextException
        with a properly formatted error message.
        """
        # Arrange
        mock_assistant = MagicMock(spec=Assistant)
        mock_assistant.id = "test-assistant-id"
        mock_assistant.name = "Test Assistant"
        mock_assistant.context = [
            Context(name="missing-context-1", context_type=ContextType.KNOWLEDGE_BASE),
            Context(name="missing-context-2", context_type=ContextType.CODE),
        ]
        missing_contexts = ["missing-context-1", "missing-context-2"]
        mock_assistant.get_deleted_context.return_value = missing_contexts

        # Act & Assert
        with patch.object(logger, 'error') as mock_logger:
            with pytest.raises(MissingContextException) as exc_info:
                AssistantService.check_context(mock_assistant)

            # Assert exception message format
            expected_message_start = "Cannot initialize assistant, missed datasource context in system:"
            assert expected_message_start in str(exc_info.value)
            for ctx in missing_contexts:
                assert f"- Datasource name: **{ctx}**" in str(exc_info.value)

            # Assert logger was called with correct message
            mock_logger.assert_called_once()
            log_msg = mock_logger.call_args[0][0]
            assert "Not all context are present in system, missed:" in log_msg
            assert str(missing_contexts) in log_msg

        # Verify the assistant.get_deleted_context() was called exactly once
        mock_assistant.get_deleted_context.assert_called_once()

    @pytest.mark.parametrize(
        "missing_contexts",
        [["single-missing-context"], ["context1", "context2", "context3"], ["context-with-special-chars!@#$%"]],
        ids=["single_context", "multiple_contexts", "special_chars"],
    )
    def test_check_context_with_different_missing_contexts(self, missing_contexts):
        """
        Tests check_context with different types of missing contexts to ensure it handles
        single items, multiple items, and special characters properly.
        """
        # Arrange
        mock_assistant = MagicMock(spec=Assistant)
        mock_assistant.id = "test-assistant-id"
        mock_assistant.name = "Test Assistant"
        mock_assistant.context = [
            Context(name=ctx, context_type=ContextType.KNOWLEDGE_BASE) for ctx in missing_contexts
        ]
        mock_assistant.get_deleted_context.return_value = missing_contexts

        # Act & Assert
        with patch.object(logger, 'error') as mock_logger:
            with pytest.raises(MissingContextException) as exc_info:
                AssistantService.check_context(mock_assistant)

            # Assert exception message format
            expected_message_start = "Cannot initialize assistant, missed datasource context in system:"
            assert expected_message_start in str(exc_info.value)
            for ctx in missing_contexts:
                assert f"- Datasource name: **{ctx}**" in str(exc_info.value)

            # Assert logger was called with correct message
            mock_logger.assert_called_once()
            log_msg = mock_logger.call_args[0][0]
            assert "Not all context are present in system, missed:" in log_msg
            assert str(missing_contexts) in log_msg

        # Verify the assistant.get_deleted_context() was called exactly once
        mock_assistant.get_deleted_context.assert_called_once()
