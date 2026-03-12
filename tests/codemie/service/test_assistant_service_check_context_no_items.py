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
from codemie.rest_api.models.assistant import Assistant
from codemie.service.assistant_service import AssistantService


class TestAssistantServiceCheckContextNoItems:
    """Tests for the check_context method of AssistantService when no context items are present"""

    def test_check_context_with_no_context_items(self):
        """
        Tests that check_context method completes successfully when the assistant has no context items
        (i.e., assistant.context is None) without raising any exceptions.
        """
        # Arrange
        mock_assistant = MagicMock(spec=Assistant)
        mock_assistant.id = "assistant-123"
        mock_assistant.name = "Test Assistant"
        mock_assistant.context = None  # No context items

        # Act
        with patch.object(logger, 'error') as mock_logger:
            result = AssistantService.check_context(mock_assistant)

        # Assert
        # Verify the assistant.get_deleted_context() is not called when context is None
        mock_assistant.get_deleted_context.assert_not_called()
        # Verify no error was logged
        mock_logger.assert_not_called()
        # The method returns None implicitly
        assert result is None

    def test_check_context_with_empty_context_list(self):
        """
        Tests that check_context method completes successfully when the assistant has an empty
        context list (i.e., assistant.context is an empty list) without raising any exceptions.
        """
        # Arrange
        mock_assistant = MagicMock(spec=Assistant)
        mock_assistant.id = "assistant-123"
        mock_assistant.name = "Test Assistant"
        mock_assistant.context = []  # Empty list of context items
        mock_assistant.get_deleted_context.return_value = []  # No deleted contexts

        # Act
        with patch.object(logger, 'error') as mock_logger:
            result = AssistantService.check_context(mock_assistant)

        # Assert
        # Verify the assistant.get_deleted_context() is NOT called for empty list
        # since the implementation treats empty list the same as None (early return)
        mock_assistant.get_deleted_context.assert_not_called()
        # Verify no error was logged
        mock_logger.assert_not_called()
        # The method returns None implicitly
        assert result is None

    @pytest.mark.parametrize("context_value", [None, []], ids=["context_none", "context_empty_list"])
    def test_check_context_with_no_context_parametrized(self, context_value):
        """
        Tests check_context with different representations of no context items (None and empty list)
        to ensure both cases are handled correctly in a parametrized test.
        """
        # Arrange
        mock_assistant = MagicMock(spec=Assistant)
        mock_assistant.id = "assistant-123"
        mock_assistant.name = "Test Assistant"
        mock_assistant.context = context_value

        # Both None and empty list should result in early return without calling get_deleted_context
        expected_get_deleted_calls = 0

        # Act
        with patch.object(logger, 'error') as mock_logger:
            result = AssistantService.check_context(mock_assistant)

        # Assert
        # Verify get_deleted_context called the appropriate number of times (0 for both None and [])
        assert mock_assistant.get_deleted_context.call_count == expected_get_deleted_calls
        # Verify no error was logged
        mock_logger.assert_not_called()
        # The method returns None implicitly
        assert result is None
