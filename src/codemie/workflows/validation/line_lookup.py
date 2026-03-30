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

"""YAML line number lookup for workflow validation errors."""

from abc import ABC, abstractmethod
from typing import Optional
import yaml

# Workflow configuration field names
FIELDS = {
    "ID": "id",
    "ASSISTANT_ID": "assistant_id",
    "TOOL_ID": "tool_id",
    "MODEL": "model",
    "CUSTOM_NODE_ID": "custom_node_id",
    "SYSTEM_PROMPT": "system_prompt",
    "TOOLS": "tools",
    "DATASOURCE_IDS": "datasource_ids",
}

# Workflow configuration section names
SECTIONS = {
    "STATES": "states",
    "ASSISTANTS": "assistants",
    "TOOLS": "tools",
    "CUSTOM_NODES": "custom_nodes",
}

# Fallback field candidates when exact field not found
FALLBACK_FIELDS = {
    SECTIONS["STATES"]: [
        FIELDS["ID"],
        FIELDS["ASSISTANT_ID"],
        FIELDS["TOOL_ID"],
        FIELDS["MODEL"],
        FIELDS["CUSTOM_NODE_ID"],
    ],
    SECTIONS["ASSISTANTS"]: [
        FIELDS["ID"],
        FIELDS["ASSISTANT_ID"],
        FIELDS["MODEL"],
        FIELDS["SYSTEM_PROMPT"],
        FIELDS["TOOLS"],
        FIELDS["DATASOURCE_IDS"],
    ],
}


def extract_line_numbers(yaml_text: str) -> dict[str, int]:
    """Extract 1-indexed line numbers from YAML. Returns {"states[0].id": 15, ...}."""
    if not yaml_text or not yaml_text.strip():
        return {}

    try:
        root_node = yaml.compose(yaml_text)
    except yaml.YAMLError:
        return {}

    line_map = {}
    _walk_yaml_node(root_node, [], line_map)
    return line_map


def _walk_yaml_node(node, path: list, line_map: dict[str, int]) -> None:
    """Recursively walk YAML nodes and extract line numbers."""
    if node is None:
        return

    if isinstance(node, yaml.MappingNode):
        for key_node, value_node in node.value:
            _process_mapping_entry(key_node, value_node, path, line_map)
    elif isinstance(node, yaml.SequenceNode):
        for i, item_node in enumerate(node.value):
            current_path = path + [i]
            path_str = _convert_path_to_string(current_path)

            if item_node.start_mark:
                line_map[path_str] = item_node.start_mark.line + 1

            _walk_yaml_node(item_node, current_path, line_map)


def _process_mapping_entry(key_node, value_node, path: list, line_map: dict[str, int]) -> None:
    """Process a single key-value pair from a YAML mapping node."""
    if not isinstance(key_node, yaml.ScalarNode):
        return
    key = key_node.value
    current_path = path + [key]
    path_str = _convert_path_to_string(current_path)

    if value_node.start_mark:
        line_map[path_str] = value_node.start_mark.line + 1

    _walk_yaml_node(value_node, current_path, line_map)


def _convert_path_to_string(path: list) -> str:
    """Convert ['states', 0, 'model'] to 'states[0].model'."""
    result = []
    for part in path:
        if isinstance(part, int):
            if result:
                result[-1] = f"{result[-1]}[{part}]"
            else:
                result.append(f"[{part}]")
        else:
            result.append(str(part))
    return ".".join(result)


class YamlLineFinderBase(ABC):
    """Abstract base for YAML line number lookup."""

    @abstractmethod
    def find_line_for_state_field(self, state_id: str, field_path: str, allow_fallback: bool = True) -> Optional[int]:
        """Find line number for state field. Returns 1-indexed line or None."""
        pass

    @abstractmethod
    def find_line_for_assistant_field(
        self, assistant_ref: str, field_path: str, allow_fallback: bool = True
    ) -> Optional[int]:
        """Find line number for assistant field. Returns 1-indexed line or None."""
        pass

    @abstractmethod
    def find_line_for_tool_field(self, tool_ref: str, field_path: str, allow_fallback: bool = True) -> Optional[int]:
        """Find line number for tool field. Returns 1-indexed line or None."""
        pass

    @abstractmethod
    def find_line_for_custom_node_field(
        self, node_ref: str, field_path: str, allow_fallback: bool = True
    ) -> Optional[int]:
        """Find line number for custom node field. Returns 1-indexed line or None."""
        pass

    @abstractmethod
    def find_line_for_top_level_field(self, field_path: str) -> Optional[int]:
        """Find line number for top-level field. Returns 1-indexed line or None."""
        pass


class YamlLineFinder(YamlLineFinderBase):
    """Finds YAML line numbers for validation errors with fallback support."""

    def __init__(
        self,
        workflow_config: dict,
        line_number_map: Optional[dict[str, int]] = None,
        yaml_text: Optional[str] = None,
    ):
        """
        Initialize with workflow config and either line_number_map OR yaml_text.

        Raises:
            ValueError: If neither line_number_map nor yaml_text provided
        """
        self.workflow_config = workflow_config

        if line_number_map is not None:
            self.line_number_map = line_number_map
        elif yaml_text is not None:
            self.line_number_map = extract_line_numbers(yaml_text)
        else:
            raise ValueError("Must provide either line_number_map or yaml_text")

    @classmethod
    def from_yaml(cls, yaml_text: str, workflow_config: dict) -> "YamlLineFinder":
        """Convenience constructor from YAML text."""
        return cls(workflow_config=workflow_config, yaml_text=yaml_text)

    def find_line_for_state_field(self, state_id: str, field_path: str, allow_fallback: bool = True) -> Optional[int]:
        """Find line number for state field."""
        return self._find_line_for_section_field(
            section_name=SECTIONS["STATES"],
            item_id=state_id,
            field_path=field_path,
            allow_fallback=allow_fallback,
        )

    def find_line_for_assistant_field(
        self, assistant_ref: str, field_path: str, allow_fallback: bool = True
    ) -> Optional[int]:
        """Find line number for assistant field."""
        return self._find_line_for_section_field(
            section_name=SECTIONS["ASSISTANTS"],
            item_id=assistant_ref,
            field_path=field_path,
            allow_fallback=allow_fallback,
        )

    def find_line_for_tool_field(self, tool_ref: str, field_path: str, allow_fallback: bool = True) -> Optional[int]:
        """Find line number for tool field."""
        return self._find_line_for_section_field(
            section_name=SECTIONS["TOOLS"],
            item_id=tool_ref,
            field_path=field_path,
            allow_fallback=allow_fallback,
        )

    def find_line_for_custom_node_field(
        self, node_ref: str, field_path: str, allow_fallback: bool = True
    ) -> Optional[int]:
        """Find line number for custom node field."""
        return self._find_line_for_section_field(
            section_name=SECTIONS["CUSTOM_NODES"],
            item_id=node_ref,
            field_path=field_path,
            allow_fallback=allow_fallback,
        )

    def find_line_for_top_level_field(self, field_path: str) -> Optional[int]:
        """Find line number for top-level field."""
        return self.line_number_map.get(field_path)

    def _find_line_for_section_field(
        self,
        section_name: str,
        item_id: str,
        field_path: str,
        allow_fallback: bool = True,
    ) -> Optional[int]:
        """Generic lookup for any section field with optional fallback."""
        if not self.workflow_config or not self.line_number_map:
            return None

        item_index = self._find_item_index(section_name, item_id)
        if item_index is None:
            return None

        # Try direct field lookup
        if field_path:
            yaml_path = f"{section_name}[{item_index}].{field_path}"
            line_num = self.line_number_map.get(yaml_path)
            if line_num:
                return line_num

        # Fallback to any line in item
        if allow_fallback:
            return self._find_any_line_in_section(section_name, item_index)

        return None

    def _find_item_index(self, section_name: str, item_id: str) -> Optional[int]:
        """Find array index for item by ID."""
        items = self.workflow_config.get(section_name, [])
        for i, item in enumerate(items):
            if item and item.get(FIELDS["ID"]) == item_id:
                return i
        return None

    def _find_any_line_in_section(self, section_name: str, item_index: int) -> Optional[int]:
        """Find any line number within section item using fallback fields."""
        fallback_fields = FALLBACK_FIELDS.get(section_name, [])
        for field in fallback_fields:
            yaml_path = f"{section_name}[{item_index}].{field}"
            if yaml_path in self.line_number_map:
                return self.line_number_map[yaml_path]

        # Last resort: find any field
        prefix = f"{section_name}[{item_index}]."
        for yaml_path, line_num in self.line_number_map.items():
            if yaml_path.startswith(prefix):
                return line_num

        return None


class NullYamlLineFinder(YamlLineFinderBase):
    """Null object returning None for all lookups."""

    def __init__(self):
        self.workflow_config = {}
        self.line_number_map = {}

    def find_line_for_state_field(self, state_id: str, field_path: str, allow_fallback: bool = True) -> None:
        return None

    def find_line_for_assistant_field(self, assistant_ref: str, field_path: str, allow_fallback: bool = True) -> None:
        return None

    def find_line_for_tool_field(self, tool_ref: str, field_path: str, allow_fallback: bool = True) -> None:
        return None

    def find_line_for_custom_node_field(self, node_ref: str, field_path: str, allow_fallback: bool = True) -> None:
        return None

    def find_line_for_top_level_field(self, field_path: str) -> None:
        return None
