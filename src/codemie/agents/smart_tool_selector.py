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

"""Smart tool selection using existing ToolkitLookupService infrastructure.

This module provides intelligent tool selection by leveraging Codemie's existing
Elasticsearch-based semantic search infrastructure via ToolkitLookupService.
"""

from typing import Optional

from langchain_core.tools import BaseTool

from codemie.configs.logger import logger

# Import directly to avoid circular import with service/__init__.py
from codemie_tools.base.models import ToolKit

from codemie.service.tools import ToolkitLookupService


class SmartToolSelector:
    """
    Selects relevant tools using semantic search via ToolkitLookupService.

    This class integrates with Codemie's existing tool infrastructure:
    - Uses ToolkitLookupService for Elasticsearch-based semantic search
    - Leverages hybrid search with reciprocal rank fusion (RRF) and reranking
    - Converts ToolKit objects back to BaseTool instances
    - Supports context-aware queries using conversation history

    Example:
        >>> tool_registry = {tool_id: tool for tool_id, tool in tools}
        >>> selector = SmartToolSelector(tool_registry, default_limit=3)
        >>> tool_ids, tools = selector.select_tools("search code files", limit=3)
    """

    def __init__(
        self,
        tool_registry: dict[str, BaseTool],
        default_limit: int = 3,
    ):
        """
        Initialize smart tool selector.

        Args:
            tool_registry: Full registry of tools (ID -> BaseTool mapping)
            default_limit: Default number of tools to select per query
        """
        self.tool_registry = tool_registry
        self.default_limit = default_limit

        # Build reverse mapping: tool name -> tool ID
        self.name_to_id = {tool.name: tool_id for tool_id, tool in tool_registry.items()}

        logger.debug(f"SmartToolSelector initialized. RegistrySize={len(tool_registry)}, DefaultLimit={default_limit}")

    def select_tools(
        self,
        query: str,
        limit: Optional[int] = None,
        history: Optional[list] = None,
    ) -> tuple[list[str], list[BaseTool]]:
        """
        Select relevant tools using semantic search.

        This method uses ToolkitLookupService to perform Elasticsearch-based
        hybrid search (semantic + keyword) with reranking to find the most
        relevant tools for the given query. It filters results to only include
        tools that are available in the assistant's tool registry.

        Args:
            query: User query or task description
            limit: Maximum number of tools to select (uses default_limit if None)
            history: Optional conversation history for building context-aware queries

        Returns:
            Tuple of (tool_ids, tool_instances) - lists of selected tool IDs and their instances

        Example:
            >>> tool_ids, tools = selector.select_tools(
            ...     query="search through code repository",
            ...     limit=3,
            ...     history=[previous_messages]
            ... )
            >>> print([t.name for t in tools])
            ['search_code', 'read_file', 'list_files']
        """
        limit = limit or self.default_limit

        # Build context-aware query if history is provided
        search_query = self._build_search_query(query, history)

        # Get list of available tool names for filtering
        available_tool_names = list(self.name_to_id.keys())

        logger.debug(
            f"SmartToolSelector: Selecting tools. "
            f"Query='{query[:100]}...', Limit={limit}, "
            f"AvailableTools={len(available_tool_names)}"
        )

        # Use existing ToolkitLookupService for semantic search with filtering
        try:
            # Get more results than needed for better selection quality
            # The reranking in ToolkitLookupService will help surface the best matches
            search_limit = limit * 2

            toolkits = ToolkitLookupService.get_tools_by_query(
                query=search_query,
                limit=search_limit,
                tool_names_filter=available_tool_names,  # Only search within available tools
            )

            # Extract tool names from toolkits
            selected_tool_names = self._extract_tool_names(toolkits, limit)

            # Convert tool names to IDs and instances
            tool_ids = []
            tool_instances = []

            for tool_name in selected_tool_names:
                if tool_id := self.name_to_id.get(tool_name):
                    if tool := self.tool_registry.get(tool_id):
                        tool_ids.append(tool_id)
                        tool_instances.append(tool)
                    else:
                        logger.warning(
                            f"SmartToolSelector: Tool ID found in name mapping but not in registry. "
                            f"Name={tool_name}, ID={tool_id}"
                        )
                else:
                    logger.debug(
                        f"SmartToolSelector: Tool from search not in registry. "
                        f"Name={tool_name}. This should not happen with filtering enabled."
                    )

            logger.info(f"SmartToolSelector: Selected {len(tool_instances)} tools: {[t.name for t in tool_instances]}")

            return tool_ids, tool_instances

        except Exception as e:
            logger.error(
                f"SmartToolSelector: Failed to select tools: {e}", exc_info=True, extra={"query": query, "limit": limit}
            )
            # Fallback: return empty selection (agent will use default tools)
            return [], []

    def _build_search_query(self, query: str, history: Optional[list]) -> str:
        """
        Build enhanced search query with conversation context.

        This method combines the current query with recent conversation history
        to provide better context for tool selection. This helps select tools
        that are relevant to the ongoing conversation flow.

        Args:
            query: Current user query
            history: Conversation history (list of messages)

        Returns:
            Enhanced query string with context

        Example:
            >>> query = "what files are there?"
            >>> history = [
            ...     HumanMessage("I'm working on a Python project"),
            ...     AIMessage("Great! How can I help?")
            ... ]
            >>> enhanced = selector._build_search_query(query, history)
            # Result: "what files are there?\n\nContext: I'm working on a Python project"
        """
        if not history:
            return query

        # Extract recent messages (last 3-5 for context)
        recent_messages = []
        history_window = history[-5:] if len(history) > 5 else history

        for msg in history_window:
            if hasattr(msg, 'content'):
                # Limit message length to avoid overwhelming the query
                content = str(msg.content)[:200]
                recent_messages.append(content)

        # Combine current query with context
        if recent_messages:
            context = " ".join(recent_messages)
            enhanced_query = f"{query}\n\nContext: {context}"

            logger.debug(
                f"SmartToolSelector: Enhanced query with context. "
                f"OriginalLength={len(query)}, EnhancedLength={len(enhanced_query)}"
            )

            return enhanced_query

        return query

    def _extract_tool_names(self, toolkits: list[ToolKit], limit: int) -> list[str]:
        """
        Extract tool names from ToolKit objects.

        ToolkitLookupService returns ToolKit objects which may contain multiple tools.
        This method extracts individual tool names up to the specified limit.

        Args:
            toolkits: List of ToolKit objects from ToolkitLookupService
            limit: Maximum number of tool names to extract

        Returns:
            List of tool names

        Example:
            >>> toolkits = [
            ...     ToolKit(toolkit="code", tools=[Tool(name="search"), Tool(name="read")]),
            ...     ToolKit(toolkit="cloud", tools=[Tool(name="list_ec2")])
            ... ]
            >>> names = selector._extract_tool_names(toolkits, limit=2)
            >>> print(names)
            ['search', 'read']
        """
        tool_names = []

        for toolkit in toolkits:
            if not hasattr(toolkit, 'tools'):
                logger.warning(f"SmartToolSelector: ToolKit object missing 'tools' attribute. Toolkit={toolkit}")
                continue

            for tool in toolkit.tools:
                if len(tool_names) >= limit:
                    break

                if hasattr(tool, 'name'):
                    tool_names.append(tool.name)
                else:
                    logger.warning(f"SmartToolSelector: Tool object missing 'name' attribute. Tool={tool}")

            if len(tool_names) >= limit:
                break

        logger.debug(f"SmartToolSelector: Extracted {len(tool_names)} tool names from {len(toolkits)} toolkits")

        return tool_names[:limit]

    def get_default_tools(self, count: int = 2) -> tuple[list[str], list[BaseTool]]:
        """
        Get default/fallback tools (first N from registry).

        This method is used when tool selection fails or when no specific query
        is available. It returns the first N tools from the registry as a fallback.

        Args:
            count: Number of tools to return

        Returns:
            Tuple of (tool_ids, tool_instances)

        Example:
            >>> tool_ids, tools = selector.get_default_tools(count=3)
            >>> print(f"Got {len(tools)} default tools")
            Got 3 default tools
        """
        tool_ids = list(self.tool_registry.keys())[:count]
        tool_instances = [self.tool_registry[tid] for tid in tool_ids]

        logger.debug(
            f"SmartToolSelector: Using default tools. "
            f"Count={len(tool_instances)}, Tools={[t.name for t in tool_instances]}"
        )

        return tool_ids, tool_instances

    def get_tools_by_category(self, category: str, limit: Optional[int] = None) -> tuple[list[str], list[BaseTool]]:
        """
        Get tools filtered by category.

        This method can be used for category-based tool selection as an alternative
        or complement to semantic search.

        Args:
            category: Tool category (e.g., "code", "cloud", "knowledge_base")
            limit: Maximum number of tools to return

        Returns:
            Tuple of (tool_ids, tool_instances)

        Note:
            This requires tools to have a 'category' metadata field.
        """
        limit = limit or self.default_limit
        tool_ids = []
        tool_instances = []

        for tool_id, tool in self.tool_registry.items():
            if len(tool_instances) >= limit:
                break

            # Check if tool has category metadata
            if hasattr(tool, 'metadata') and isinstance(tool.metadata, dict):
                tool_category = tool.metadata.get('category', '')
                if tool_category == category:
                    tool_ids.append(tool_id)
                    tool_instances.append(tool)

        logger.debug(
            f"SmartToolSelector: Selected {len(tool_instances)} tools by category '{category}': {[t.name for t in tool_instances]}"
        )

        return tool_ids, tool_instances
