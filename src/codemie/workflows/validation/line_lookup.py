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
from typing import Any

from codemie.workflows.contracts.models import ContractLocation, LocationSection
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

    return _line_maps(root_node)[0]


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
    def find_line_for_state_field(self, state_id: str, field_path: str, allow_fallback: bool = True) -> int | None:
        """Find line number for state field. Returns 1-indexed line or None."""
        pass

    @abstractmethod
    def find_line_for_assistant_field(
        self, assistant_ref: str, field_path: str, allow_fallback: bool = True
    ) -> int | None:
        """Find line number for assistant field. Returns 1-indexed line or None."""
        pass

    @abstractmethod
    def find_line_for_tool_field(self, tool_ref: str, field_path: str, allow_fallback: bool = True) -> int | None:
        """Find line number for tool field. Returns 1-indexed line or None."""
        pass

    @abstractmethod
    def find_line_for_custom_node_field(
        self, node_ref: str, field_path: str, allow_fallback: bool = True
    ) -> int | None:
        """Find line number for custom node field. Returns 1-indexed line or None."""
        pass

    @abstractmethod
    def find_line_for_top_level_field(self, field_path: str) -> int | None:
        """Find line number for top-level field. Returns 1-indexed line or None."""
        pass

    def find_contract_line(self, location: ContractLocation) -> int | None:  # noqa: ARG002 - argument is part of the lookup interface
        """Exact typed lookup; null finders have no source metadata."""
        return None


class YamlLineFinder(YamlLineFinderBase):
    """Finds YAML line numbers for validation errors with fallback support."""

    def __init__(
        self,
        workflow_config: dict,
        line_number_map: dict[str, int] | None = None,
        yaml_text: str | None = None,
        token_line_map: dict[tuple[str | int, ...], int] | None = None,
    ):
        """
        Initialize with workflow config and either line_number_map OR yaml_text.

        Raises:
            ValueError: If neither line_number_map nor yaml_text provided
        """
        self.workflow_config = workflow_config
        self.token_line_map = token_line_map or {}

        if line_number_map is not None:
            self.line_number_map = line_number_map
        elif yaml_text is not None:
            try:
                root = yaml.compose(yaml_text)
            except yaml.YAMLError:
                root = None
            self.line_number_map, self.token_line_map = _line_maps(root)
        else:
            raise ValueError("Must provide either line_number_map or yaml_text")

    @classmethod
    def from_yaml(cls, yaml_text: str, workflow_config: dict) -> "YamlLineFinder":
        """Convenience constructor from YAML text."""
        return cls(workflow_config=workflow_config, yaml_text=yaml_text)

    def find_line_for_state_field(self, state_id: str, field_path: str, allow_fallback: bool = True) -> int | None:
        """Find line number for state field."""
        return self._find_line_for_section_field(
            section_name=SECTIONS["STATES"],
            item_id=state_id,
            field_path=field_path,
            allow_fallback=allow_fallback,
        )

    def find_line_for_assistant_field(
        self, assistant_ref: str, field_path: str, allow_fallback: bool = True
    ) -> int | None:
        """Find line number for assistant field."""
        return self._find_line_for_section_field(
            section_name=SECTIONS["ASSISTANTS"],
            item_id=assistant_ref,
            field_path=field_path,
            allow_fallback=allow_fallback,
        )

    def find_line_for_tool_field(self, tool_ref: str, field_path: str, allow_fallback: bool = True) -> int | None:
        """Find line number for tool field."""
        return self._find_line_for_section_field(
            section_name=SECTIONS["TOOLS"],
            item_id=tool_ref,
            field_path=field_path,
            allow_fallback=allow_fallback,
        )

    def find_line_for_custom_node_field(
        self, node_ref: str, field_path: str, allow_fallback: bool = True
    ) -> int | None:
        """Find line number for custom node field."""
        return self._find_line_for_section_field(
            section_name=SECTIONS["CUSTOM_NODES"],
            item_id=node_ref,
            field_path=field_path,
            allow_fallback=allow_fallback,
        )

    def find_line_for_top_level_field(self, field_path: str) -> int | None:
        """Find line number for top-level field."""
        return self.line_number_map.get(field_path)

    def _find_line_for_section_field(
        self,
        section_name: str,
        item_id: str,
        field_path: str,
        allow_fallback: bool = True,
    ) -> int | None:
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

    def find_contract_line(self, location: ContractLocation) -> int | None:
        """Use exact keys and integer indices, with parent fields as the only fallback.

        An explicitly empty location addresses its owner. A missing non-empty field never
        falls back to the owner or an unrelated sibling field.
        """
        prefix: tuple[str | int, ...] = ()
        if location.section is not LocationSection.WORKFLOW:
            index = self._find_item_index(location.section.value, location.owner_id)
            if index is None:
                return None
            prefix = (location.section.value, index)
        segments = location.segments
        if not segments:
            return self.token_line_map.get(prefix)
        for length in range(len(segments), 0, -1):
            line = self.token_line_map.get(prefix + segments[:length])
            if line is not None:
                return line
        return None

    def _find_item_index(self, section_name: str, item_id: str) -> int | None:
        """Find array index for item by ID."""
        items = self.workflow_config.get(section_name, [])
        if not isinstance(items, list):
            return None
        for i, item in enumerate(items):
            if isinstance(item, dict) and item.get(FIELDS["ID"]) == item_id:
                return i
        return None

    def _find_any_line_in_section(self, section_name: str, item_index: int) -> int | None:
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


def _line_maps(root: Any) -> tuple[dict[str, int], dict[tuple[str | int, ...], int]]:
    """Build legacy display paths and exact tokens from the same YAML node marks."""
    legacy: dict[str, int] = {}
    tokens: dict[tuple[str | int, ...], int] = {}
    _record_lines(root, (), frozenset(), legacy, tokens)
    return legacy, tokens


def _record_lines(
    node: Any,
    path: tuple[str | int, ...],
    active: frozenset[int],
    legacy: dict[str, int],
    tokens: dict[tuple[str | int, ...], int],
) -> None:
    """Record the 1-based start line of `node` and its children, depth first; `active` stops alias cycles."""
    if node is None or id(node) in active:
        return
    active = active | {id(node)}
    if node.start_mark:
        tokens[path] = node.start_mark.line + 1
        if path:
            legacy[_convert_path_to_string(list(path))] = tokens[path]
    for child, child_path in _child_nodes(node, path):
        _record_lines(child, child_path, active, legacy, tokens)


def _child_nodes(node: Any, path: tuple[str | int, ...]) -> list[tuple[Any, tuple[str | int, ...]]]:
    """Children of a mapping (scalar keys only) or a sequence, with their paths, in document order."""
    if isinstance(node, yaml.MappingNode):
        return [(value, (*path, key.value)) for key, value in node.value if isinstance(key, yaml.ScalarNode)]
    if isinstance(node, yaml.SequenceNode):
        return [(value, (*path, index)) for index, value in enumerate(node.value)]
    return []


class SourceMetadata:
    """Parsed source and both line maps, built from a single safe YAML node tree."""

    def __init__(self, text: str):
        self.parsed: Any = None
        self.parse_error: yaml.YAMLError | None = None
        self.finder: YamlLineFinderBase = NullYamlLineFinder()
        self.line_number_map: dict[str, int] = {}
        loader = yaml.SafeLoader(text)
        try:
            root = loader.get_single_node()
            self.parsed = loader.construct_document(root) if root is not None else None
            self.line_number_map, tokens = _line_maps(root)
            if isinstance(self.parsed, dict):
                self.finder = YamlLineFinder(self.parsed, self.line_number_map, token_line_map=tokens)
        except yaml.YAMLError as exc:
            self.parse_error = exc
        finally:
            loader.dispose()


class ValidationSourceContext:
    """Request-local source metadata. Exact text identity separates submitted and normalized YAML."""

    def __init__(self):
        self._sources: dict[str, SourceMetadata] = {}

    def source(self, text: str) -> SourceMetadata:
        if text not in self._sources:
            self._sources[text] = SourceMetadata(text)
        return self._sources[text]
