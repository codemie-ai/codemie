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
from codemie.rest_api.models.assistant import Assistant, Context, ContextType
from codemie.service.assistant_service import AssistantService


class TestAssistantServiceCheckContextValid:
    """Tests for the check_context method of AssistantService when all context is valid"""

    def test_check_context_with_all_valid_context(self):
        """
        Tests that check_context method completes successfully when all context items
        referenced in the assistant are valid and present in the system.
        """
        # Arrange
        mock_assistant = MagicMock(spec=Assistant)
        mock_assistant.id = "assistant-123"
        mock_assistant.name = "Test Assistant"
        mock_assistant.context = [
            Context(name="test-context-1", context_type=ContextType.KNOWLEDGE_BASE),
            Context(name="test-context-2", context_type=ContextType.CODE),
        ]
        # Configure get_deleted_context to return an empty list (all contexts are valid)
        mock_assistant.get_deleted_context.return_value = []

        # Act
        with patch.object(logger, 'error') as mock_logger:
            result = AssistantService.check_context(mock_assistant)

        # Assert
        # Verify the assistant.get_deleted_context() was called exactly once
        mock_assistant.get_deleted_context.assert_called_once()
        # Verify no error was logged
        mock_logger.assert_not_called()
        # The method returns None implicitly
        assert result is None

    @pytest.mark.parametrize(
        "contexts",
        [
            # Test with single context item of each type
            [Context(name="knowledge-base-ctx", context_type=ContextType.KNOWLEDGE_BASE)],
            [Context(name="code-ctx", context_type=ContextType.CODE)],
            [Context(name="provider-ctx", context_type=ContextType.PROVIDER)],
            # Test with multiple context items of mixed types
            [
                Context(name="kb-ctx-1", context_type=ContextType.KNOWLEDGE_BASE),
                Context(name="code-ctx-1", context_type=ContextType.CODE),
                Context(name="provider-ctx-1", context_type=ContextType.PROVIDER),
            ],
            # Test with a large number of context items
            [Context(name=f"ctx-{i}", context_type=ContextType.KNOWLEDGE_BASE) for i in range(10)],
        ],
        ids=["single_kb", "single_code", "single_provider", "mixed_types", "large_number"],
    )
    def test_check_context_with_various_valid_contexts(self, contexts):
        """
        Tests check_context with different combinations and numbers of valid contexts
        to ensure it handles all cases correctly.
        """
        # Arrange
        mock_assistant = MagicMock(spec=Assistant)
        mock_assistant.id = "assistant-123"
        mock_assistant.name = "Test Assistant"
        mock_assistant.context = contexts
        # Configure get_deleted_context to return an empty list (all contexts are valid)
        mock_assistant.get_deleted_context.return_value = []

        # Act
        with patch.object(logger, 'error') as mock_logger:
            result = AssistantService.check_context(mock_assistant)

        # Assert
        # Verify the assistant.get_deleted_context() was called exactly once
        mock_assistant.get_deleted_context.assert_called_once()
        # Verify no error was logged
        mock_logger.assert_not_called()
        # The method returns None implicitly
        assert result is None
