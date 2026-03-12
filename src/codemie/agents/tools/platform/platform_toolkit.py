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

"""Platform monitoring and analytics toolkit.

This toolkit provides admin-only tools for monitoring assistants, conversations, and spending.
All tools require admin privileges.
"""

from typing import List

from codemie_tools.base.codemie_tool import CodeMieTool
from codemie_tools.base.models import ToolKit, Tool, ToolSet
from langchain_core.tools import BaseTool
from pydantic import BaseModel

from codemie.agents.tools.base import BaseToolkit
from codemie.agents.tools.platform.platform_tool import (
    GetAssistantsTool,
    GetConversationMetricsTool,
    GetRawConversationsTool,
    GetSpendingTool,
    GetKeySpendingTool,
    GetConversationAnalyticsTool,
)
from codemie.agents.tools.platform.tools_vars import (
    GET_ASSISTANTS_TOOL,
    GET_CONVERSATION_METRICS_TOOL,
    GET_RAW_CONVERSATIONS_TOOL,
    GET_SPENDING_TOOL,
    GET_KEY_SPENDING_TOOL,
    GET_CONVERSATION_ANALYTICS_TOOL,
)
from codemie.rest_api.security.user import User


class PlatformToolkitUI(ToolKit):
    """UI model for platform toolkit."""

    toolkit: str = ToolSet.PLATFORM_TOOLS
    tools: List[Tool] = [
        Tool.from_metadata(GET_ASSISTANTS_TOOL),
        Tool.from_metadata(GET_CONVERSATION_METRICS_TOOL),
        Tool.from_metadata(GET_RAW_CONVERSATIONS_TOOL),
        Tool.from_metadata(GET_SPENDING_TOOL),
        Tool.from_metadata(GET_KEY_SPENDING_TOOL),
        Tool.from_metadata(GET_CONVERSATION_ANALYTICS_TOOL),
    ]


class PlatformToolkit(BaseModel, BaseToolkit):
    """Toolkit for platform monitoring and analytics (admin-only).

    This toolkit follows the same pattern as IdeToolkit, creating separate
    tool instances for each platform analytics operation.
    """

    user: User

    @classmethod
    def get_tools_ui_info(cls, is_admin: bool = False):
        if is_admin:
            return PlatformToolkitUI().model_dump()

        # Otherwise, return only safe tools
        return ToolKit(
            toolkit=ToolSet.PLATFORM_TOOLS,
            tools=[
                Tool.from_metadata(GET_ASSISTANTS_TOOL),
                Tool.from_metadata(GET_CONVERSATION_METRICS_TOOL),
                Tool.from_metadata(GET_SPENDING_TOOL),
                Tool.from_metadata(GET_CONVERSATION_ANALYTICS_TOOL),
            ],
        ).model_dump()

    def get_tools(self) -> List[BaseTool]:
        """
        Get list of all tools in this toolkit.

        Returns:
            List of separate tool instances (GetAssistantsTool, GetConversationMetricsTool, etc.)

        Note:
            Each tool is a separate class that extends CodeMieTool.
            This follows the same pattern as IdeToolkit.
        """
        tools: list[CodeMieTool] = [
            GetAssistantsTool(user=self.user),
            GetConversationMetricsTool(user=self.user),
            GetSpendingTool(user=self.user),
            GetConversationAnalyticsTool(user=self.user),
        ]
        if self.user.is_admin:
            tools.extend(
                [
                    GetRawConversationsTool(user=self.user),
                    GetKeySpendingTool(user=self.user),
                ]
            )
        return tools
