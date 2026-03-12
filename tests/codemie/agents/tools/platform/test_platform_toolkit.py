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

"""Unit tests for PlatformToolkit."""

import unittest
from unittest.mock import patch

from codemie.agents.tools.platform.platform_tool import (
    GetAssistantsTool,
    GetConversationMetricsTool,
    GetSpendingTool,
    GetRawConversationsTool,
    GetKeySpendingTool,
)
from codemie.agents.tools.platform.platform_toolkit import PlatformToolkit, PlatformToolkitUI
from codemie.rest_api.security.user import User


class TestPlatformToolkitUI(unittest.TestCase):
    """Test suite for PlatformToolkitUI"""

    def test_platform_toolkit_ui_structure(self):
        """Test PlatformToolkitUI has correct structure"""
        # Arrange & Act
        from codemie_tools.base.models import ToolSet

        toolkit_ui = PlatformToolkitUI()

        # Assert
        self.assertEqual(toolkit_ui.toolkit, ToolSet.PLATFORM_TOOLS.value)
        self.assertIsNotNone(toolkit_ui.tools)
        self.assertGreater(len(toolkit_ui.tools), 0)

        # Verify tool names are present
        tool_names = [tool.name for tool in toolkit_ui.tools]
        self.assertIn("get_assistants", tool_names)
        self.assertIn("get_conversation_metrics", tool_names)
        self.assertIn("get_raw_conversations", tool_names)
        self.assertIn("get_spending", tool_names)
        self.assertIn("get_key_spending", tool_names)


class TestPlatformToolkit(unittest.TestCase):
    """Test suite for PlatformToolkit"""

    def setUp(self):
        """Set up test fixtures"""
        self.admin_user = User(id="admin-user", name="Admin", username="admin", roles=["admin"])
        self.regular_user = User(id="regular-user", name="Regular", username="regular", roles=[])

    def test_get_tools_ui_info_for_admin(self):
        """Test get_tools_ui_info returns all tools for admin user"""
        # Act
        from codemie_tools.base.models import ToolSet

        result = PlatformToolkit.get_tools_ui_info(is_admin=True)

        # Assert
        self.assertIsNotNone(result)
        self.assertEqual(result["toolkit"], ToolSet.PLATFORM_TOOLS.value)
        self.assertIn("tools", result)

        tool_names = [tool["name"] for tool in result["tools"]]
        self.assertIn("get_assistants", tool_names)
        self.assertIn("get_conversation_metrics", tool_names)
        self.assertIn("get_raw_conversations", tool_names)
        self.assertIn("get_spending", tool_names)
        self.assertIn("get_key_spending", tool_names)

    def test_get_tools_ui_info_for_non_admin(self):
        """Test get_tools_ui_info returns limited tools for non-admin user"""
        # Act
        from codemie_tools.base.models import ToolSet

        result = PlatformToolkit.get_tools_ui_info(is_admin=False)

        # Assert
        self.assertIsNotNone(result)
        self.assertEqual(result["toolkit"], ToolSet.PLATFORM_TOOLS.value)
        self.assertIn("tools", result)

        tool_names = [tool["name"] for tool in result["tools"]]
        # Non-admin should have access to these tools
        self.assertIn("get_assistants", tool_names)
        self.assertIn("get_conversation_metrics", tool_names)
        self.assertIn("get_spending", tool_names)

        # Non-admin should NOT have access to these tools
        self.assertNotIn("get_raw_conversations", tool_names)
        self.assertNotIn("get_key_spending", tool_names)

    @patch("codemie.rest_api.security.user.config.ENV", "production")
    def test_get_tools_for_admin_user(self):
        """Test get_tools returns all tools for admin user"""
        # Arrange
        toolkit = PlatformToolkit(user=self.admin_user)

        # Act
        tools = toolkit.get_tools()

        # Assert
        self.assertIsNotNone(tools)
        self.assertEqual(len(tools), 6)

        # Verify all tool types are present
        tool_types = [type(tool) for tool in tools]
        self.assertIn(GetAssistantsTool, tool_types)
        self.assertIn(GetConversationMetricsTool, tool_types)
        self.assertIn(GetSpendingTool, tool_types)
        self.assertIn(GetRawConversationsTool, tool_types)
        self.assertIn(GetKeySpendingTool, tool_types)

    @patch("codemie.rest_api.security.user.config.ENV", "production")
    def test_get_tools_for_regular_user(self):
        """Test get_tools returns limited tools for regular user"""
        # Arrange
        toolkit = PlatformToolkit(user=self.regular_user)

        # Act
        tools = toolkit.get_tools()

        # Assert
        self.assertIsNotNone(tools)
        self.assertEqual(len(tools), 4)

        # Verify tool types present for regular user
        tool_types = [type(tool) for tool in tools]
        self.assertIn(GetAssistantsTool, tool_types)
        self.assertIn(GetConversationMetricsTool, tool_types)
        self.assertIn(GetSpendingTool, tool_types)

        # Verify admin-only tools are NOT present
        self.assertNotIn(GetRawConversationsTool, tool_types)
        self.assertNotIn(GetKeySpendingTool, tool_types)

    @patch("codemie.rest_api.security.user.config.ENV", "production")
    def test_get_tools_user_context_passed(self):
        """Test get_tools passes user context to tool instances"""
        # Arrange
        toolkit = PlatformToolkit(user=self.admin_user)

        # Act
        tools = toolkit.get_tools()

        # Assert
        for tool in tools:
            self.assertEqual(tool.user, self.admin_user)
