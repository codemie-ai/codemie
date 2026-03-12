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

"""State management for smart tool selection in LangGraph agents.

This module provides state management and tool registry capabilities for agents
that dynamically select relevant tools based on semantic search.
"""

import uuid
from typing import Annotated, Optional

from langchain_core.tools import BaseTool
from langgraph.graph import MessagesState

from codemie.configs.logger import logger


def add_tool_ids(left: list[str], right: list[str]) -> list[str]:
    """
    Reducer function to add new tool IDs without duplicates.

    This is used as a state reducer in LangGraph to accumulate selected tools
    across multiple tool selection steps.

    Args:
        left: Existing list of tool IDs
        right: New tool IDs to add

    Returns:
        Combined list with no duplicates
    """
    existing = set(left)
    return left + [tid for tid in right if tid not in existing]


class SmartToolState(MessagesState):
    """
    Extended state for agents with smart tool selection.

    Inherits from MessagesState to maintain conversation history while adding
    fields for dynamic tool management.

    IMPORTANT: This state only stores tool IDs, not tool instances, to ensure
    serialization compatibility with LangGraph's checkpoint system. Tool instances
    are maintained in the graph's closure scope.

    Attributes:
        messages: Standard message history (inherited from MessagesState)
        selected_tool_ids: List of tool IDs that have been selected and are available
        query_context: Optional context string for tool selection queries
    """

    selected_tool_ids: Annotated[list[str], add_tool_ids]
    query_context: Optional[str]


class ToolRegistry:
    """
    Manages tool instances and provides lookup capabilities.

    This class wraps the existing Codemie tool infrastructure and provides
    a unified interface for tool selection and retrieval. It maintains mappings
    between tool IDs, tool names, and tool instances for efficient lookup.
    """

    def __init__(self, tools: list[BaseTool]):
        """
        Initialize tool registry with a list of tools.

        Args:
            tools: List of all available BaseTool instances
        """
        self.tools = tools
        self.registry: dict[str, BaseTool] = {}
        self.name_to_id: dict[str, str] = {}
        self.id_to_name: dict[str, str] = {}

        self._build_registry()

        logger.debug(f"ToolRegistry initialized with {len(self.tools)} tools")

    def _build_registry(self):
        """Build internal mappings between tool IDs, names, and instances."""
        for tool in self.tools:
            # Use tool name as base for ID with short hash for uniqueness
            # This makes IDs more readable in logs while ensuring uniqueness
            tool_id = f"{tool.name}_{uuid.uuid4().hex[:8]}"

            self.registry[tool_id] = tool
            self.name_to_id[tool.name] = tool_id
            self.id_to_name[tool_id] = tool.name

            logger.debug(f"ToolRegistry: Registered tool. Name={tool.name}, ID={tool_id}")

    def get_tool_by_id(self, tool_id: str) -> Optional[BaseTool]:
        """
        Get tool instance by ID.

        Args:
            tool_id: Unique tool identifier

        Returns:
            BaseTool instance or None if not found
        """
        return self.registry.get(tool_id)

    def get_tools_by_ids(self, tool_ids: list[str]) -> list[BaseTool]:
        """
        Get multiple tool instances by their IDs.

        Args:
            tool_ids: List of tool identifiers

        Returns:
            List of BaseTool instances (excludes IDs not found in registry)
        """
        tools = []
        for tid in tool_ids:
            if tool := self.registry.get(tid):
                tools.append(tool)
            else:
                logger.warning(f"ToolRegistry: Tool ID not found in registry. ID={tid}")

        return tools

    def get_tool_id(self, tool_name: str) -> Optional[str]:
        """
        Get tool ID by tool name.

        Args:
            tool_name: Name of the tool

        Returns:
            Tool ID or None if tool name not found
        """
        return self.name_to_id.get(tool_name)

    def get_tool_name(self, tool_id: str) -> Optional[str]:
        """
        Get tool name by tool ID.

        Args:
            tool_id: Unique tool identifier

        Returns:
            Tool name or None if tool ID not found
        """
        return self.id_to_name.get(tool_id)

    def get_all_tool_ids(self) -> list[str]:
        """
        Get all tool IDs in the registry.

        Returns:
            List of all tool IDs
        """
        return list(self.registry.keys())

    def get_all_tool_names(self) -> list[str]:
        """
        Get all tool names in the registry.

        Returns:
            List of all tool names
        """
        return list(self.name_to_id.keys())

    def __len__(self) -> int:
        """Return the number of tools in the registry."""
        return len(self.registry)

    def __contains__(self, tool_id: str) -> bool:
        """Check if a tool ID exists in the registry."""
        return tool_id in self.registry
