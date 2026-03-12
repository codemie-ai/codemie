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

"""Unit tests for platform tool implementations."""

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, MagicMock, patch

import pytest

from codemie.agents.tools.platform.platform_tool import (
    GetKeySpendingTool,
    _parse_date_filters,
    _extract_tools_invoked,
    _calculate_total_spending,
)
from codemie.core.exceptions import InvalidFilterCombinationError, UnauthorizedPlatformAccessError
from codemie.enterprise.loader import KeySpendingInfo, HAS_LITELLM
from codemie.rest_api.security.user import User


# ==================== Helper Function Tests ====================


class TestParseDateFilters(unittest.TestCase):
    """Test suite for _parse_date_filters helper function"""

    def test_parse_date_filters_since_date(self):
        """Test _parse_date_filters with since_date parameter"""
        # Arrange
        since_date = "2024-01-01T00:00:00Z"
        last_n_days = None

        # Act
        result = _parse_date_filters(since_date, last_n_days)

        # Assert
        self.assertIsNotNone(result)
        self.assertEqual(result.year, 2024)
        self.assertEqual(result.month, 1)
        self.assertEqual(result.day, 1)

    def test_parse_date_filters_last_n_days(self):
        """Test _parse_date_filters with last_n_days parameter"""
        # Arrange
        since_date = None
        last_n_days = 7

        # Act
        result = _parse_date_filters(since_date, last_n_days)

        # Assert
        self.assertIsNotNone(result)
        expected_date = datetime.now(timezone.utc) - timedelta(days=7)
        # Allow 1 second difference for test execution time
        self.assertAlmostEqual(result.timestamp(), expected_date.timestamp(), delta=1)
        # Ensure result is timezone-aware (UTC)
        self.assertIsNotNone(result.tzinfo)
        self.assertEqual(result.tzinfo, timezone.utc)

    def test_parse_date_filters_no_filter(self):
        """Test _parse_date_filters with no filters"""
        # Arrange
        since_date = None
        last_n_days = None

        # Act
        result = _parse_date_filters(since_date, last_n_days)

        # Assert
        self.assertIsNone(result)

    def test_parse_date_filters_both_raises_exception(self):
        """Test _parse_date_filters raises exception when both filters provided"""
        # Arrange
        since_date = "2024-01-01T00:00:00Z"
        last_n_days = 7

        # Act & Assert
        with self.assertRaises(InvalidFilterCombinationError):
            _parse_date_filters(since_date, last_n_days)


class TestExtractToolsInvoked(unittest.TestCase):
    """Test suite for _extract_tools_invoked helper function"""

    def test_extract_tools_invoked_full_mode_with_thoughts(self):
        """Test _extract_tools_invoked in full_mode with thoughts"""
        # Arrange
        msg = Mock()
        thought1 = Mock()
        thought1.author_name = "search_tool"
        thought1.input_text = "search query"

        thought2 = Mock()
        thought2.author_name = "calculator_tool"
        thought2.input_text = "2 + 2"

        msg.thoughts = [thought1, thought2]

        # Act
        result = _extract_tools_invoked(msg, full_mode=True)

        # Assert
        self.assertIsNotNone(result)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["tool_name"], "search_tool")
        self.assertEqual(result[0]["tool_input"], "search query")
        self.assertEqual(result[1]["tool_name"], "calculator_tool")
        self.assertEqual(result[1]["tool_input"], "2 + 2")

    def test_extract_tools_invoked_not_full_mode(self):
        """Test _extract_tools_invoked returns None when not in full_mode"""
        # Arrange
        msg = Mock()
        msg.thoughts = [Mock()]

        # Act
        result = _extract_tools_invoked(msg, full_mode=False)

        # Assert
        self.assertIsNone(result)

    def test_extract_tools_invoked_no_thoughts(self):
        """Test _extract_tools_invoked with no thoughts"""
        # Arrange
        msg = Mock()
        msg.thoughts = None

        # Act
        result = _extract_tools_invoked(msg, full_mode=True)

        # Assert
        self.assertIsNone(result)

    def test_extract_tools_invoked_empty_thoughts(self):
        """Test _extract_tools_invoked with empty thoughts list"""
        # Arrange
        msg = Mock()
        msg.thoughts = []

        # Act
        result = _extract_tools_invoked(msg, full_mode=True)

        # Assert
        self.assertIsNone(result)

    def test_extract_tools_invoked_thought_without_author_name(self):
        """Test _extract_tools_invoked skips thoughts without author_name"""
        # Arrange
        msg = Mock()
        thought1 = Mock()
        thought1.author_name = "valid_tool"
        thought1.input_text = "input"

        thought2 = Mock()
        thought2.author_name = None  # No author_name

        msg.thoughts = [thought1, thought2]

        # Act
        result = _extract_tools_invoked(msg, full_mode=True)

        # Assert
        self.assertIsNotNone(result)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["tool_name"], "valid_tool")

    def test_extract_tools_invoked_thought_without_input_text(self):
        """Test _extract_tools_invoked handles thought without input_text attribute"""
        # Arrange
        msg = Mock()
        thought = Mock()
        thought.author_name = "tool_name"
        # Don't set input_text attribute
        del thought.input_text

        msg.thoughts = [thought]

        # Act
        result = _extract_tools_invoked(msg, full_mode=True)

        # Assert
        self.assertIsNotNone(result)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["tool_name"], "tool_name")
        self.assertIsNone(result[0]["tool_input"])


class TestCalculateTotalSpending(unittest.TestCase):
    """Test suite for _calculate_total_spending helper function"""

    def test_calculate_total_spending_with_litellm(self):
        """Test _calculate_total_spending uses LiteLLM spend when available"""
        # Arrange
        conversation_result = Mock()
        conversation_result.total_input_tokens = 1000
        conversation_result.total_output_tokens = 500
        conversation_result.total_money_spent = 50.0

        workflow_data = {
            "total_input_tokens": 200,
            "total_output_tokens": 100,
            "total_money_spent": 10.0,
        }

        # Act
        total_input, total_output, total_money = _calculate_total_spending(conversation_result, workflow_data)

        # Assert
        self.assertEqual(total_input, 1200)  # 1000 + 200
        self.assertEqual(total_output, 600)  # 500 + 100

    def test_calculate_total_spending_without_litellm(self):
        """Test _calculate_total_spending uses combined metrics when LiteLLM not available"""
        # Arrange
        conversation_result = Mock()
        conversation_result.total_input_tokens = 1000
        conversation_result.total_output_tokens = 500
        conversation_result.total_money_spent = 50.0

        workflow_data = {
            "total_input_tokens": 200,
            "total_output_tokens": 100,
            "total_money_spent": 10.0,
        }

        # Act
        total_input, total_output, total_money = _calculate_total_spending(conversation_result, workflow_data)

        # Assert
        self.assertEqual(total_input, 1200)
        self.assertEqual(total_output, 600)
        self.assertEqual(total_money, 60.0)  # 50.0 + 10.0 from metrics


# ==================== Tool Tests ====================


@pytest.mark.skipif(not HAS_LITELLM, reason="Enterprise package not installed - LiteLLM not available")
class TestGetKeySpendingTool(unittest.TestCase):
    """Test suite for GetKeySpendingTool"""

    def setUp(self):
        """Set up test fixtures"""
        self.admin_user = User(id="admin-user", name="Admin", username="admin", roles=["admin"])
        self.regular_user = User(id="regular-user", name="Regular", username="regular", roles=[])

    @patch("codemie.rest_api.security.user.config.ENV", "production")
    def test_execute_admin_all_keys(self):
        """Test GetKeySpendingTool execute for admin user requesting all keys"""
        # Arrange
        tool = GetKeySpendingTool(user=self.admin_user)

        mock_keys = [
            KeySpendingInfo(spend=100.0, key_alias="key1"),
            KeySpendingInfo(spend=200.0, key_alias="key2"),
        ]

        # Patch get_litellm_service_or_none to return a mock service
        with patch("codemie.enterprise.litellm.get_litellm_service_or_none") as mock_get_service:
            mock_litellm_service = MagicMock()
            mock_litellm_service.get_all_keys_spending.return_value = mock_keys
            mock_get_service.return_value = mock_litellm_service

            # Act
            result = tool.execute(key_aliases=None, include_details=True, page=1, size=100)

            # Assert
            # After the fix, we always pass include_details=True to fetch full data
            mock_litellm_service.get_all_keys_spending.assert_called_once_with(include_details=True, page=1, size=100)
            self.assertIsNotNone(result)
            # Result is JSON string, parse to verify
            import json

            parsed = json.loads(result)
            self.assertEqual(parsed["total_keys"], 2)
            self.assertEqual(parsed["total_spend_across_keys"], 300.0)
            self.assertEqual(len(parsed["keys"]), 2)

    @patch("codemie.rest_api.security.user.config.ENV", "production")
    def test_execute_admin_specific_keys(self):
        """Test GetKeySpendingTool execute for admin user requesting specific keys"""
        # Arrange
        tool = GetKeySpendingTool(user=self.admin_user)

        mock_keys = [
            KeySpendingInfo(spend=100.0, key_alias="key1"),
        ]

        with patch("codemie.enterprise.litellm.get_litellm_service_or_none") as mock_get_service:
            mock_litellm_service = MagicMock()
            mock_litellm_service.get_key_info.return_value = mock_keys
            mock_get_service.return_value = mock_litellm_service

            # Act
            result = tool.execute(key_aliases=["key1"], include_details=False, page=1, size=50)

            # Assert
            # After the fix, we always pass include_details=True to fetch full data
            mock_litellm_service.get_key_info.assert_called_once_with(["key1"], include_details=True, page=1, size=50)
            self.assertIsNotNone(result)

    @patch("codemie.rest_api.security.user.config.ENV", "production")
    def test_execute_non_admin_raises_exception(self):
        """Test GetKeySpendingTool execute raises exception for non-admin user"""
        # Arrange
        tool = GetKeySpendingTool(user=self.regular_user)

        # Act & Assert
        with self.assertRaises(UnauthorizedPlatformAccessError):
            tool.execute(key_aliases=None)

    @patch("codemie.rest_api.security.user.config.ENV", "production")
    def test_execute_with_custom_pagination(self):
        """Test GetKeySpendingTool execute with custom pagination parameters"""
        # Arrange
        tool = GetKeySpendingTool(user=self.admin_user)

        mock_keys = []

        with patch("codemie.enterprise.litellm.get_litellm_service_or_none") as mock_get_service:
            mock_litellm_service = MagicMock()
            mock_litellm_service.get_all_keys_spending.return_value = mock_keys
            mock_get_service.return_value = mock_litellm_service

            # Act
            result = tool.execute(key_aliases=None, include_details=False, page=2, size=25)

            # Assert
            # After the fix, we always pass include_details=True to fetch full data
            mock_litellm_service.get_all_keys_spending.assert_called_once_with(include_details=True, page=2, size=25)
            self.assertIsNotNone(result)

            import json

            parsed = json.loads(result)
            self.assertEqual(parsed["total_keys"], 0)
            self.assertEqual(parsed["total_spend_across_keys"], 0.0)
