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
Transformers for converting validation errors to structured formats.

This module provides classes for transforming various error types into
WorkflowValidationErrorDetail format with appropriate metadata enrichment.
"""

import uuid
import yaml
from pydantic import ValidationError

from codemie.core.workflow_models import WorkflowConfig
from codemie.workflows.validation.models import WorkflowValidationErrorDetail, MCPMeta
from codemie.workflows.validation.line_lookup import (
    YamlLineFinderBase,
    YamlLineFinder,
    extract_line_numbers,
)


class YamlPath:
    """YAML path segments."""

    ASSISTANTS = "assistants"
    STATES = "states"
    TOOLS = "tools"
    CUSTOM_NODES = "custom_nodes"
    MCP_SERVERS = "mcp_servers"
    NAME = "name"
    ID = "id"


# Top-level sections with 'id' field
SECTIONS = (YamlPath.ASSISTANTS, YamlPath.STATES, YamlPath.TOOLS, YamlPath.CUSTOM_NODES)


class PydanticErrorTransformer:
    """
    Transforms Pydantic ValidationError into WorkflowValidationErrorDetail format.

    Enriches errors with metadata (MCP server names) and line numbers.
    """

    def __init__(self, validation_error: ValidationError, workflow_config: WorkflowConfig):
        """
        Initialize transformer with validation error and workflow config.

        Args:
            validation_error: Pydantic ValidationError from parse_execution_config()
            workflow_config: WorkflowConfig instance containing yaml_config
        """
        self.validation_error = validation_error
        self.workflow_config = workflow_config
        self._yaml_data: dict | None = None
        self._line_finder: YamlLineFinderBase | None = None

    def transform(self, state_id: str | None = None) -> list[dict]:
        """
        Transform Pydantic ValidationError to WorkflowValidationErrorDetail format.

        Args:
            state_id: Optional state ID to associate with all errors

        Returns:
            List of error dicts in WorkflowValidationErrorDetail format
        """
        self._yaml_data = yaml.safe_load(self.workflow_config.yaml_config) or {}
        line_number_map = extract_line_numbers(self.workflow_config.yaml_config)
        self._line_finder = YamlLineFinder(self._yaml_data, line_number_map)

        return [
            self._transform_single_error(err, state_id).model_dump(exclude_none=True)
            for err in self.validation_error.errors()
        ]

    def _transform_single_error(self, error: dict, state_id: str | None = None) -> WorkflowValidationErrorDetail:
        """
        Transform a single Pydantic error dict to WorkflowValidationErrorDetail.

        Args:
            error: Single error dict from ValidationError.errors()
            state_id: Optional state ID to associate with this error. If None, will be extracted from error location.

        Returns:
            WorkflowValidationErrorDetail instance
        """
        loc = error.get("loc", ())
        loc_list = list(loc)

        # Extract state_id from error location if not provided
        if state_id is None:
            state_id = self._extract_state_id(loc_list)

        return WorkflowValidationErrorDetail(
            id=str(uuid.uuid4()),
            message="Validation error",
            path=self._extract_field_path(loc_list),
            details=error.get("msg", ""),
            meta=self._extract_mcp_meta(loc_list),
            config_line=self._extract_line_number(loc_list),
            state_id=state_id,
        )

    def _extract_field_path(self, loc_list: list) -> str:
        """
        Extract field path from Pydantic error location.

        For section-relative paths (assistants, states, tools, custom_nodes, mcp_servers),
        returns path relative to that section.

        Examples:
        - ["retry_policy", "max_interval"] -> "retry_policy.max_interval"
        - ["mcp_servers", 0, "config", "args"] -> "config.args"
        - ["assistants", 0, "name"] -> "name"
        - ["model"] -> "model"

        Args:
            loc_list: Location as list

        Returns:
            Field path string (dotted notation for nested fields)
        """
        if not loc_list:
            return ""

        # Check for MCP server paths
        if YamlPath.MCP_SERVERS in loc_list:
            return self._extract_relative_path(loc_list, YamlPath.MCP_SERVERS)

        # Check for section-relative paths
        for section in SECTIONS:
            if section in loc_list:
                return self._extract_relative_path(loc_list, section)

        # Top-level path: join non-integer parts
        field_parts = [str(part) for part in loc_list if not isinstance(part, int)]
        if field_parts:
            return ".".join(field_parts)
        return str(loc_list[-1]) if loc_list else ""

    @staticmethod
    def _extract_relative_path(loc_list: list, section: str) -> str:
        """
        Extract path relative to a section.

        Args:
            loc_list: Location as list
            section: Section name (e.g., "assistants", "mcp_servers")

        Returns:
            Relative path string
        """
        section_idx = loc_list.index(section)
        # Skip section name and index, return path after that
        if section_idx + 2 < len(loc_list):
            field_parts = [str(part) for part in loc_list[section_idx + 2 :] if not isinstance(part, int)]
            if field_parts:
                return ".".join(field_parts)

        # Return leaf field if no path after section
        return str(loc_list[-1]) if loc_list else ""

    def _extract_line_number(self, loc_list: list) -> int | None:
        """
        Extract line number from YAML config using YamlLineFinder.

        Args:
            loc_list: Location as list

        Returns:
            Line number or None if not found
        """
        if not self._line_finder or not loc_list:
            return None

        # For MCP server errors, find assistant ID and use assistant field lookup
        if YamlPath.MCP_SERVERS in loc_list:
            assistant_id = self._find_assistant_by_nested_item(YamlPath.MCP_SERVERS, loc_list)
            if assistant_id:
                field_path = self._build_field_path_with_brackets(loc_list)
                return self._line_finder.find_line_for_assistant_field(assistant_id, field_path)

        # For section-level errors (states, assistants), lookup by item ID
        for section, finder_method in [
            (YamlPath.STATES, "find_line_for_state_field"),
            (YamlPath.ASSISTANTS, "find_line_for_assistant_field"),
        ]:
            line_num = self._lookup_line_for_section(loc_list, section, finder_method)
            if line_num:
                return line_num

        # For top-level errors, use top-level field lookup with fallback
        field_path = self._build_field_path_with_brackets(loc_list)
        line_num = self._line_finder.find_line_for_top_level_field(field_path)

        # Fallback to parent path if full path not found
        if not line_num and "." in field_path:
            parent_path = field_path.rsplit(".", 1)[0]
            line_num = self._line_finder.find_line_for_top_level_field(parent_path)

        return line_num

    def _lookup_line_for_section(self, loc_list: list, section: str, finder_method: str) -> int | None:
        """
        Look up line number for error in a specific section.

        Args:
            loc_list: Location as list
            section: Section name (e.g., "states", "assistants")
            finder_method: Name of line finder method to call

        Returns:
            Line number or None
        """
        if section not in loc_list:
            return None

        try:
            section_idx = loc_list.index(section)
            if section_idx + 1 >= len(loc_list) or not isinstance(loc_list[section_idx + 1], int):
                return None

            item_idx = loc_list[section_idx + 1]
            items = self._yaml_data.get(section, [])

            if item_idx >= len(items) or not isinstance(items[item_idx], dict):
                return None

            item_id = items[item_idx].get(YamlPath.ID)
            if not item_id:
                return None

            # Build field path (skip section and index)
            field_path = self._build_field_path_with_brackets(loc_list[section_idx + 2 :])
            method = getattr(self._line_finder, finder_method)
            return method(item_id, field_path)

        except (ValueError, IndexError, KeyError, AttributeError):
            return None

    @staticmethod
    def _build_field_path_with_brackets(loc_parts: list) -> str:
        """
        Build field path string with bracket notation for arrays.

        Example: ["mcp_servers", 1, "config", "args"] -> "mcp_servers[1].config.args"

        Args:
            loc_parts: List of location parts (strings and integers)

        Returns:
            Field path string with bracket notation
        """
        if not loc_parts:
            return ""

        result = []
        for part in loc_parts:
            if isinstance(part, int):
                if result:
                    result[-1] = f"{result[-1]}[{part}]"
                else:
                    result.append(f"[{part}]")
            else:
                result.append(str(part))
        return ".".join(result)

    def _extract_mcp_meta(self, loc_list: list) -> MCPMeta | None:
        """
        Extract MCP metadata from error location.

        Args:
            loc_list: Location as list

        Returns:
            MCPMeta instance or None if not an MCP error
        """
        if not self._yaml_data or YamlPath.MCP_SERVERS not in loc_list:
            return None

        try:
            mcp_idx = loc_list.index(YamlPath.MCP_SERVERS)
            if mcp_idx + 1 >= len(loc_list) or not isinstance(loc_list[mcp_idx + 1], int):
                return None

            # Try full path navigation first
            mcp_path = tuple(loc_list[: mcp_idx + 2])
            mcp_server = self._navigate_yaml_path(mcp_path)
            mcp_name = mcp_server.get(YamlPath.NAME) if isinstance(mcp_server, dict) else None
            if mcp_name:
                return MCPMeta(mcp_name=mcp_name)

            # If relative path (no assistants prefix), search all assistants
            if YamlPath.ASSISTANTS not in loc_list:
                server_idx = loc_list[mcp_idx + 1]
                mcp_name = self._find_mcp_name_in_assistants(server_idx)
                if mcp_name:
                    return MCPMeta(mcp_name=mcp_name)

        except (ValueError, IndexError, KeyError, TypeError):
            pass

        return None

    def _find_assistant_by_nested_item(self, nested_section: str, loc_list: list) -> str | None:
        """
        Find assistant ID that contains a nested item at given index.

        Args:
            nested_section: Nested section name (e.g., "mcp_servers")
            loc_list: Location as list

        Returns:
            Assistant ID or None
        """
        if not self._yaml_data or nested_section not in loc_list:
            return None

        try:
            section_idx = loc_list.index(nested_section)
            if section_idx + 1 >= len(loc_list) or not isinstance(loc_list[section_idx + 1], int):
                return None

            item_idx = loc_list[section_idx + 1]

            for assistant in self._yaml_data.get(YamlPath.ASSISTANTS, []):
                if not isinstance(assistant, dict):
                    continue

                nested_items = assistant.get(nested_section, [])
                if isinstance(nested_items, list) and item_idx < len(nested_items):
                    return assistant.get(YamlPath.ID)

        except (ValueError, IndexError, KeyError):
            pass

        return None

    def _find_mcp_name_in_assistants(self, server_idx: int) -> str | None:
        """
        Find MCP server name by searching all assistants.

        Args:
            server_idx: MCP server index

        Returns:
            MCP server name or None
        """
        for assistant in self._yaml_data.get(YamlPath.ASSISTANTS, []):
            if not isinstance(assistant, dict):
                continue

            mcp_servers = assistant.get(YamlPath.MCP_SERVERS, [])
            if isinstance(mcp_servers, list) and server_idx < len(mcp_servers):
                mcp_server = mcp_servers[server_idx]
                if isinstance(mcp_server, dict):
                    mcp_name = mcp_server.get(YamlPath.NAME)
                    if mcp_name:
                        return mcp_name

        return None

    def _extract_state_id(self, loc_list: list) -> str | None:
        """
        Extract state ID that references the item with error.

        For errors in assistants/tools/custom_nodes, finds which state uses that item.
        For errors in states section, extracts the state ID directly.
        For MCP server errors (relative path), finds which assistant contains the MCP server.

        Args:
            loc_list: Location as list

        Returns:
            State ID or None
        """
        if not self._yaml_data or not loc_list:
            return None

        # Direct state error: extract state ID from path
        if YamlPath.STATES in loc_list:
            try:
                state_idx_pos = loc_list.index(YamlPath.STATES)
                if state_idx_pos + 1 < len(loc_list) and isinstance(loc_list[state_idx_pos + 1], int):
                    item_idx = loc_list[state_idx_pos + 1]
                    states = self._yaml_data.get(YamlPath.STATES, [])
                    if item_idx < len(states) and isinstance(states[item_idx], dict):
                        return states[item_idx].get(YamlPath.ID)
            except (ValueError, IndexError, KeyError):
                pass
            return None

        # MCP server error (relative path): find assistant containing this MCP server
        if YamlPath.MCP_SERVERS in loc_list and YamlPath.ASSISTANTS not in loc_list:
            assistant_id = self._find_assistant_by_nested_item(YamlPath.MCP_SERVERS, loc_list)
            if assistant_id:
                return self._find_state_by_assistant_id(assistant_id)

        # Assistant error: find state that uses this assistant
        if YamlPath.ASSISTANTS in loc_list:
            assistant_id = self._extract_item_id(loc_list, YamlPath.ASSISTANTS)
            if assistant_id:
                return self._find_state_by_assistant_id(assistant_id)

        # Tool error: find state that uses this tool
        if YamlPath.TOOLS in loc_list:
            tool_id = self._extract_item_id(loc_list, YamlPath.TOOLS)
            if tool_id:
                return self._find_state_by_tool_id(tool_id)

        # Custom node error: find state that uses this custom node
        if YamlPath.CUSTOM_NODES in loc_list:
            node_id = self._extract_item_id(loc_list, YamlPath.CUSTOM_NODES)
            if node_id:
                return self._find_state_by_node_id(node_id)

        return None

    def _extract_item_id(self, loc_list: list, section: str) -> str | None:
        """
        Extract item ID from a section in the location path.

        Args:
            loc_list: Location as list
            section: Section name (e.g., "assistants", "tools")

        Returns:
            Item ID or None
        """
        if section not in loc_list:
            return None

        try:
            section_idx = loc_list.index(section)
            if section_idx + 1 < len(loc_list) and isinstance(loc_list[section_idx + 1], int):
                item_idx = loc_list[section_idx + 1]
                items = self._yaml_data.get(section, [])
                if item_idx < len(items) and isinstance(items[item_idx], dict):
                    return items[item_idx].get(YamlPath.ID)
        except (ValueError, IndexError, KeyError):
            pass

        return None

    def _find_state_by_assistant_id(self, assistant_id: str) -> str | None:
        """
        Find state ID that uses the given assistant.

        Args:
            assistant_id: Assistant ID to search for

        Returns:
            State ID or None
        """
        for state in self._yaml_data.get(YamlPath.STATES, []):
            if not isinstance(state, dict):
                continue
            if state.get("assistant_id") == assistant_id:
                return state.get(YamlPath.ID)
        return None

    def _find_state_by_tool_id(self, tool_id: str) -> str | None:
        """
        Find state ID that uses the given tool.

        Args:
            tool_id: Tool ID to search for

        Returns:
            State ID or None
        """
        for state in self._yaml_data.get(YamlPath.STATES, []):
            if not isinstance(state, dict):
                continue
            tools = state.get("tools", [])
            if isinstance(tools, list) and tool_id in tools:
                return state.get(YamlPath.ID)
        return None

    def _find_state_by_node_id(self, node_id: str) -> str | None:
        """
        Find state ID that uses the given custom node.

        Args:
            node_id: Custom node ID to search for

        Returns:
            State ID or None
        """
        for state in self._yaml_data.get(YamlPath.STATES, []):
            if not isinstance(state, dict):
                continue
            if state.get("node") == node_id:
                return state.get(YamlPath.ID)
        return None

    def _navigate_yaml_path(self, path: tuple) -> any:
        """
        Navigate through parsed YAML structure using a path tuple.

        Args:
            path: Path tuple like ("assistants", 0, "mcp_servers", 1)

        Returns:
            The value at the path, or None if path is invalid
        """
        current = self._yaml_data
        for part in path:
            if current is None:
                return None

            if isinstance(part, int):
                if isinstance(current, list) and 0 <= part < len(current):
                    current = current[part]
                else:
                    return None
            elif isinstance(current, dict):
                current = current.get(part)
            else:
                return None

        return current
