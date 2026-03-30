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
Provides functionality for validating Workflow Execution Config in YAML format

This module uses `jsonschema`, which does not support cross-reference validation.
To address this, the following approach is applied:

1) YAML format is validated

2) A standard validator is applied to checks the config against the schema. The schema is defined
in a YAML file located at `WORKFLOW_EXECUTION_CONFIG_SCHEMA_YAML_FILE_PATH`

3) A cross-reference validation function `_validate_workflow_execution_config_cross_references`
ensures that all references point to defined resources. For example, if a state references
an `assistant_id`, the corresponding assistant with the same `id` must be defined in the
`assistants` section.

Functions:
    validate_workflow_execution_config_yaml(workflow_config_yaml: str): Validates Workflow Execution Config
    in YAML format

Resources:
    Validation schema: YAML file located at `WORKFLOW_EXECUTION_CONFIG_SCHEMA_YAML_FILE_PATH`

Constants:
    WORKFLOW_EXECUTION_CONFIG_SCHEMA_YAML_FILE_PATH: Specifes the path to the validation schema file

Exceptions:
    WorkflowExecutionConfigError
    WorkflowExecutionParsingError
    SchemaError
    WorkflowExecutionConfigSchemaValidationError
    WorkflowExecutionConfigCrossReferenceValidationError
"""

from collections import deque
from typing import List, Optional
import uuid
import yaml
from jsonschema import Draft202012Validator, ValidationError
from pathlib import Path
from codemie.workflows.constants import WorkflowErrorType
from codemie.workflows.validation.models import WorkflowValidationErrorDetail, CrossRefError
from codemie.workflows.validation.line_lookup import (
    YamlLineFinder,
    NullYamlLineFinder,
    extract_line_numbers,
    SECTIONS,
)


class WorkflowExecutionConfigError(yaml.YAMLError):
    def __init__(self, message="YAML error occurred"):
        super().__init__(message)


class WorkflowExecutionParsingError(WorkflowExecutionConfigError):
    def __init__(self, message="Error occured while attempting to read YAML"):
        super().__init__(message)

    def to_dict(self) -> dict:
        """Convert parsing error to structured dictionary format."""
        return {
            "error_type": WorkflowErrorType.PARSING.value,
            "message": "Invalid YAML format",
            "errors": [
                {
                    "id": str(uuid.uuid4()),
                    "message": "YAML parsing failed",
                    "details": str(self),
                    "path": "",
                }
            ],
        }


class SchemaError(WorkflowExecutionConfigError):
    def __init__(self, message="Error occured while attempting to read and validate schema"):
        super().__init__(message)


class WorkflowExecutionConfigSchemaValidationError(WorkflowExecutionConfigError):
    def __init__(
        self,
        schema_validation_errors: List[ValidationError],
        workflow_config: dict = None,
        line_number_map: dict[str, int] = None,
        yaml_line_finder: Optional[YamlLineFinder] = None,
    ):
        self.schema_validation_errors = schema_validation_errors
        self.workflow_config = workflow_config or {}

        # Use YamlLineFinder if provided, otherwise create from line_number_map
        if yaml_line_finder:
            self.line_finder = yaml_line_finder
        elif workflow_config and line_number_map:
            self.line_finder = YamlLineFinder(workflow_config, line_number_map)
        else:
            self.line_finder = NullYamlLineFinder()

        messages = []
        for i, error in enumerate(schema_validation_errors):
            messages.append(
                f"{i + 1}) {WorkflowExecutionConfigSchemaValidationError._format_validation_error_message(error)}"
            )
        super().__init__(("\n").join(messages))

    def to_dict(self) -> dict:
        """Convert validation errors to structured dictionary format with line numbers."""
        errors = [self._build_error_detail(error) for error in self.schema_validation_errors]
        return {
            "error_type": WorkflowErrorType.SCHEMA_VALIDATION.value,
            "message": "Configuration contains validation errors",
            "errors": errors,
        }

    def _build_error_detail(self, error: ValidationError) -> dict:
        state_id = self._extract_state_id(error.path, self.workflow_config)
        path_list = list(error.path)
        error_detail = WorkflowValidationErrorDetail(
            id=str(uuid.uuid4()),
            message=self._generate_short_message(error),
            path=self._get_display_field_path(error, state_id),
            details=self._extract_error_details(error),
            state_id=state_id,
            config_line=self._get_config_line(error, path_list, state_id),
        )
        return error_detail.model_dump(exclude_none=True)

    def _get_display_field_path(self, error: ValidationError, state_id: str | None) -> str:
        if error.validator == "required" and isinstance(error.validator_value, list) and error.validator_value:
            missing_field = error.validator_value[0]
            context_path = self._extract_field_path(error.path, state_id) if state_id is not None else ""
            return f"{context_path}.{missing_field}" if context_path else missing_field
        return self._extract_field_path(error.path, state_id)

    def _get_config_line(self, error: ValidationError, path_list: list, state_id: str | None) -> int | None:
        if len(path_list) >= 2:
            lookup_field_path = self._extract_field_path_for_line_lookup(error.path)
            config_line = self._get_section_config_line(path_list, state_id, lookup_field_path)
            if config_line is not None:
                return config_line
        return self.line_finder.find_line_for_top_level_field(self._build_bracket_path(path_list))

    def _get_section_config_line(self, path_list: list, state_id: str | None, lookup_field_path: str) -> int | None:
        section = path_list[0]
        if section == SECTIONS["STATES"] and state_id:
            return self.line_finder.find_line_for_state_field(state_id, lookup_field_path)
        section_finders = {
            SECTIONS["ASSISTANTS"]: self.line_finder.find_line_for_assistant_field,
            SECTIONS["TOOLS"]: self.line_finder.find_line_for_tool_field,
            SECTIONS["CUSTOM_NODES"]: self.line_finder.find_line_for_custom_node_field,
        }
        if section in section_finders and isinstance(path_list[1], int):
            item_id = self._get_item_id_from_section(self.workflow_config, section, path_list[1])
            if item_id:
                return section_finders[section](item_id, lookup_field_path)
        return None

    @staticmethod
    def _build_bracket_path(path_list: list) -> str:
        parts = []
        for part in path_list:
            if isinstance(part, int):
                if parts:
                    parts[-1] += f"[{part}]"
                else:
                    parts.append(f"[{part}]")
            else:
                parts.append(str(part))
        return ".".join(parts) if parts else ""

    @staticmethod
    def _extract_state_id(path: deque, workflow_config: dict) -> str | None:
        """
        Extract state ID for errors.

        For errors in states section: extract state ID directly from path
        For errors in assistants/tools/custom_nodes: find which state uses that item

        Args:
            path: jsonschema validation error path
            workflow_config: Parsed workflow configuration dict

        Returns:
            State ID string or None for top-level errors
        """
        path_list = list(path)
        if not path_list or not workflow_config:
            return None

        # Check if error is in states array - extract state ID directly
        if len(path_list) >= 2 and path_list[0] == SECTIONS["STATES"] and isinstance(path_list[1], int):
            return WorkflowExecutionConfigSchemaValidationError._get_item_id_from_section(
                workflow_config, SECTIONS["STATES"], path_list[1]
            )

        # For other sections, find which state uses this item
        section_finders = {
            SECTIONS["ASSISTANTS"]: WorkflowExecutionConfigSchemaValidationError._find_state_using_assistant,
            SECTIONS["TOOLS"]: WorkflowExecutionConfigSchemaValidationError._find_state_using_tool,
            SECTIONS["CUSTOM_NODES"]: WorkflowExecutionConfigSchemaValidationError._find_state_using_node,
        }

        for section, finder in section_finders.items():
            if len(path_list) >= 2 and path_list[0] == section and isinstance(path_list[1], int):
                item_id = WorkflowExecutionConfigSchemaValidationError._get_item_id_from_section(
                    workflow_config, section, path_list[1]
                )
                if item_id:
                    return finder(workflow_config, item_id)

        return None

    @staticmethod
    def _get_item_id_from_section(workflow_config: dict, section: str, index: int) -> str | None:
        """
        Get item ID from a section by index.

        Args:
            workflow_config: Parsed workflow configuration dict
            section: Section name (e.g., "states", "assistants")
            index: Item index

        Returns:
            Item ID or None
        """
        items = workflow_config.get(section, [])
        if index < len(items) and items[index]:
            return items[index].get("id")
        return None

    @staticmethod
    def _find_state_using_assistant(workflow_config: dict, assistant_id: str) -> str | None:
        """Find state that uses the given assistant."""
        # Search both states and orphaned_states
        for section in [SECTIONS["STATES"], "orphaned_states"]:
            for state in workflow_config.get(section, []):
                if state.get("assistant_id") == assistant_id:
                    return state.get("id")
        return None

    @staticmethod
    def _find_state_using_tool(workflow_config: dict, tool_id: str) -> str | None:
        """Find state that uses the given tool."""
        # Search both states and orphaned_states
        for section in [SECTIONS["STATES"], "orphaned_states"]:
            for state in workflow_config.get(section, []):
                # Check tool_id field (for tool nodes)
                if state.get("tool_id") == tool_id:
                    return state.get("id")
                # Check tools array (for tools used by assistant states)
                tools = state.get("tools", [])
                if isinstance(tools, list) and tool_id in tools:
                    return state.get("id")
        return None

    @staticmethod
    def _find_state_using_node(workflow_config: dict, node_id: str) -> str | None:
        """Find state that uses the given custom node."""
        # Search both states and orphaned_states
        for section in [SECTIONS["STATES"], "orphaned_states"]:
            for state in workflow_config.get(section, []):
                if state.get("custom_node_id") == node_id:
                    return state.get("id")
        return None

    @staticmethod
    def _join_non_int_parts(parts: list) -> str:
        """Join non-integer path parts with dots."""
        return ".".join(str(p) for p in parts if not isinstance(p, int))

    @staticmethod
    def _build_bracketed_path(parts: list) -> str:
        """Build dotted path with bracket notation for array indices (e.g. 'mcp_servers[1].config')."""
        result = []
        for part in parts:
            if isinstance(part, int):
                if result:
                    result[-1] += f"[{part}]"
            else:
                result.append(str(part))
        return ".".join(result)

    @staticmethod
    def _extract_field_path(path: deque, state_id: str | None = None) -> str:
        """
        Extract field path from jsonschema path (for display).

        For state-level errors (state_id provided), return just the leaf field name (e.g., 'model')
        For section-level errors (tools, custom_nodes, assistants), return path without section prefix
        For top-level errors (no state_id), return the full dotted path (e.g., 'retry_policy.max_attempts')

        Examples:
            - ['states', 0, 'model'] with state_id='assistant_3' -> 'model'
            - ['tools', 0, 'tool'] -> 'tool'
            - ['custom_nodes', 0, 'name'] -> 'name'
            - ['assistants', 0, 'model'] -> 'model'
            - ['retry_policy', 'max_attempts'] with state_id=None -> 'retry_policy.max_attempts'
            - ['max_concurrency'] with state_id=None -> 'max_concurrency'

        Args:
            path: jsonschema validation error path
            state_id: State ID if error is in a state, None for top-level errors

        Returns:
            Field path appropriate for display context
        """
        path_list = list(path)
        if not path_list:
            return ""

        first = path_list[0]
        is_indexed_section = len(path_list) >= 2 and isinstance(path_list[1], int)

        if state_id is not None:
            if first == "states" and is_indexed_section:
                return WorkflowExecutionConfigSchemaValidationError._build_bracketed_path(path_list[2:])
            return next((str(p) for p in reversed(path_list) if not isinstance(p, int)), "")

        if first in ('tools', 'custom_nodes', 'assistants') and is_indexed_section:
            return WorkflowExecutionConfigSchemaValidationError._join_non_int_parts(path_list[2:]) or str(first)

        return WorkflowExecutionConfigSchemaValidationError._join_non_int_parts(path_list)

    @staticmethod
    def _extract_field_path_for_line_lookup(path: deque) -> str:
        """
        Extract field path for line number lookup.

        For items in sections with arrays (states/assistants/tools/custom_nodes),
        extract the field path after the section and index:
        - ['states', 0, 'model'] -> 'model'
        - ['assistants', 0, 'system_prompt'] -> 'system_prompt'
        - ['tools', 0, 'tool'] -> 'tool'
        - ['custom_nodes', 0, 'name'] -> 'name'

        For top-level errors, build full path with bracket notation:
        - ['max_concurrency'] -> 'max_concurrency'
        - ['retry_policy', 'max_attempts'] -> 'retry_policy.max_attempts'

        Args:
            path: jsonschema validation error path

        Returns:
            Field path for line lookup
        """
        path_list = list(path)
        if not path_list:
            return ""

        sections_with_arrays = ("states", "assistants", "tools", "custom_nodes")
        if len(path_list) >= 2 and path_list[0] in sections_with_arrays and isinstance(path_list[1], int):
            field_parts = [str(p) for p in path_list[2:] if not isinstance(p, int)]
            return ".".join(field_parts) if field_parts else path_list[0]

        return WorkflowExecutionConfigSchemaValidationError._build_bracket_path(path_list)

    @staticmethod
    def _generate_short_message(error: ValidationError) -> str:
        """
        Extract concise error message from jsonschema ValidationError.

        Maps common validation error types to user-friendly messages.

        Args:
            error: jsonschema ValidationError

        Returns:
            Short, actionable error message
        """
        validator = error.validator

        if validator == "required":
            return "Missing required field"
        elif validator == "type":
            expected_type = error.validator_value
            return f"Invalid type (expected {expected_type})"
        elif validator in ("anyOf", "oneOf"):
            return "Invalid value"
        elif validator == "not":
            return "Invalid combination"
        elif validator == "enum":
            return "Invalid value"
        elif validator == "minimum" or validator == "maximum":
            return "Value out of range"
        elif validator == "minLength" or validator == "maxLength":
            return "Invalid length"
        elif validator == "pattern":
            return "Invalid format"
        else:
            return "Validation error"

    _VALIDATOR_DETAIL_FORMATTERS: dict = {
        "enum": lambda v: f"Allowed values: {', '.join(f'{x}' for x in v)}",
        "minimum": lambda v: f"Must be at least {v}",
        "maximum": lambda v: f"Must be at most {v}",
        "minLength": lambda v: f"Must be at least {v} characters",
        "maxLength": lambda v: f"Must be at most {v} characters",
        "pattern": lambda v: f"Must match pattern: {v}",
    }

    @staticmethod
    def _extract_error_details(error: ValidationError) -> str | None:
        """
        Extract detailed error context if available.

        Returns None if error.message is sufficient, otherwise returns
        additional context or explanation.

        Args:
            error: jsonschema ValidationError

        Returns:
            Detailed error message or None
        """
        validator = error.validator
        value = error.validator_value

        if validator == "required" and isinstance(value, list):
            return f"Required fields: {', '.join(f'{f}' for f in value)}"
        if validator == "type":
            return None  # Type info already in short message
        if validator in ("anyOf", "oneOf"):
            return WorkflowExecutionConfigSchemaValidationError._extract_any_of_details(value)
        if validator == "not" and isinstance(value, dict) and "required" in value:
            fields = " and ".join(f"'{f}'" for f in value.get("required", []))
            return f"Cannot set {fields} at the same time"
        formatter = WorkflowExecutionConfigSchemaValidationError._VALIDATOR_DETAIL_FORMATTERS.get(validator)
        if formatter:
            return formatter(value)
        return error.message if error.message and len(error.message) > 10 else None

    @staticmethod
    def _extract_any_of_details(schemas: list) -> str | None:
        """Extract required field options from anyOf/oneOf schemas."""
        required_fields: set[str] = set()
        for schema in schemas:
            if "required" in schema:
                required_fields.update(schema["required"])
        if required_fields:
            return f"Must set one of: {' or '.join(f'{f}' for f in required_fields)}"
        return None

    @staticmethod
    def _get_item_id(array_name: str, index: int, workflow_config: dict) -> str | None:
        """Get the ID of an item in an array by index. Returns None if ID not found."""
        if not workflow_config:
            return None

        items = workflow_config.get(array_name, [])
        if index < len(items) and items[index]:
            return items[index].get("id")

        return None

    @staticmethod
    def _format_path(path_deque: deque, workflow_config: dict = None) -> str:
        path_parts = []

        for part in path_deque:
            if isinstance(part, int):
                prev = path_parts[-1] if path_parts else ""
                item_id = WorkflowExecutionConfigSchemaValidationError._get_item_id(prev, part, workflow_config)
                if item_id:
                    path_parts[-1] = f"{prev}.{item_id}"
                else:
                    path_parts[-1] = f"{prev}[{part}]"
            else:
                path_parts.append(str(part))

        return ".".join(path_parts)

    @staticmethod
    def _format_required_error(prefix: str, value: list) -> str:
        required_fields_str = ", ".join(value)
        return f"{prefix}'{required_fields_str}' is required"

    @staticmethod
    def _format_anyof_oneof_error(prefix: str, validator: str, value: list) -> str:
        required_fields = set()
        key = None

        for schema in value:
            if 'required' in schema:
                fields = schema['required']
            elif 'dependentRequired' in schema:
                dependent_required = schema['dependentRequired']
                keys = list(dependent_required.keys())
                if keys:
                    key = keys[0]
                    fields = dependent_required[key]
                else:
                    fields = []
            else:
                fields = []
            required_fields.update(fields)

        required_fields_str = " or ".join(f"'{field}'" for field in required_fields)

        if key is not None and validator == "anyOf":
            return f"{prefix}'{key}' is set, so {required_fields_str} also must be set"
        elif validator == "anyOf":
            return f"{prefix}at least one of {required_fields_str} must be set"
        else:
            return f"{prefix}one and only one of {required_fields_str} must be set"

    @staticmethod
    def _format_not_error(prefix: str, value: dict) -> str:
        fields_str = " and ".join(f"'{v}'" for v in value.get("required", []))
        return f"{prefix}{fields_str} cannot be set at the same time"

    @staticmethod
    def _format_validation_error_message(error: ValidationError, include_path: bool = True) -> str:
        path = WorkflowExecutionConfigSchemaValidationError._format_path(error.path)
        validator = error.validator
        value = error.validator_value
        prefix = f"In '{path}': " if include_path else ""

        try:
            if validator == "required" and isinstance(value, list):
                return WorkflowExecutionConfigSchemaValidationError._format_required_error(prefix, value)
            elif validator in ("anyOf", "oneOf") and isinstance(value, list):
                return WorkflowExecutionConfigSchemaValidationError._format_anyof_oneof_error(prefix, validator, value)
            elif validator == "not" and isinstance(value, dict) and 'required' in value:
                return WorkflowExecutionConfigSchemaValidationError._format_not_error(prefix, value)
            elif validator == "type":
                return f"{prefix}{value} is expected"
            else:
                return f"{prefix}config does not conform to the required schema"
        except (AttributeError, KeyError, ValueError):
            return f"{prefix}config does not conform to the required schema"


class WorkflowExecutionConfigCrossReferenceValidationError(WorkflowExecutionConfigError):
    def __init__(
        self,
        cross_ref_errors: List[CrossRefError],
        line_number_map: dict[str, int] = None,
        workflow_config: dict = None,
    ):
        self.cross_ref_errors = cross_ref_errors
        self.line_number_map = line_number_map or {}
        self.workflow_config = workflow_config or {}

        # Initialize YamlLineFinder for line number lookups
        if workflow_config and line_number_map:
            self.line_finder = YamlLineFinder(workflow_config, line_number_map)
        else:
            self.line_finder = NullYamlLineFinder()

        messages = []
        for i, error in enumerate(self.cross_ref_errors, start=len(messages) + 1):
            messages.append(WorkflowExecutionConfigCrossReferenceValidationError._format_cross_ref_error(i, error))
        super().__init__(("\n").join(messages))

    def to_dict(self) -> dict:
        """Convert cross-reference errors to structured dictionary format with line numbers."""
        errors = []

        for error in self.cross_ref_errors:
            # Map error.referrer to state_id
            state_id = error.referrer

            path = error.key if error.key else ""

            # Find line number using YamlLineFinder
            config_line = self.line_finder.find_line_for_state_field(
                state_id=state_id,
                field_path=error.key,  # Use full path for lookup (e.g., "next.state_id")
            )

            if error.ref == '':
                message = f"{error.entity.capitalize()} is required"
                details = None
            else:
                message = "Invalid reference"
                details = f"{error.entity.capitalize()} '{error.ref}' not found"

            error_detail = WorkflowValidationErrorDetail(
                id=str(uuid.uuid4()),
                message=message,
                details=details,
                state_id=state_id,
                path=path,
                config_line=config_line,
            )

            errors.append(error_detail.model_dump(exclude_none=True))

        return {
            "error_type": WorkflowErrorType.CROSS_REFERENCE_VALIDATION.value,
            "message": "Configuration contains cross-reference errors",
            "errors": errors,
        }

    @staticmethod
    def _format_cross_ref_error(i: int, error: CrossRefError) -> str:
        return f"{i}) In '{error.referrer}' state: '{error.key}' key references \
            undefined '{error.ref}' {error.entity}"


def _load_schema_from_file(file_path: str):
    try:
        with open(file_path, 'r') as file:
            schema = yaml.safe_load(file)
    except (FileNotFoundError, yaml.YAMLError):
        raise SchemaError

    if Draft202012Validator.check_schema(schema):
        raise SchemaError

    return schema


WORKFLOW_EXECUTION_CONFIG_SCHEMA_YAML_FILE_PATH = Path(__file__).parent.parent / "execution_config_schema.yaml"
WORKFLOW_EXECUTION_CONFIG_SCHEMA = _load_schema_from_file(WORKFLOW_EXECUTION_CONFIG_SCHEMA_YAML_FILE_PATH)


def _extract_ids(workflow_config: dict, key: str) -> List[str]:
    return [v["id"] for v in (workflow_config.get(key, []) or []) if v and "id" in v and v["id"]]


def _validate_workflow_execution_config_schema(schema: dict, workflow_config: dict) -> List[ValidationError]:
    """
    Validates execution workflow config over schema
    """

    validator = Draft202012Validator(schema)

    validation_errors = list(validator.iter_errors(workflow_config))

    return validation_errors


def _check_ref(obj, key, valid_ids, state_id, kind, errors, key_path=None):
    if key in obj:
        value = obj[key]
        if value not in valid_ids:
            errors.append(CrossRefError(state_id, key_path, value, kind))


def _validate_workflow_execution_config_cross_references(workflow_config: dict) -> List[CrossRefError]:
    assistant_ids = _extract_ids(workflow_config, "assistants")
    tool_ids = _extract_ids(workflow_config, "tools")
    custom_node_ids = _extract_ids(workflow_config, "custom_nodes")
    state_ids = _extract_ids(workflow_config, "states") + ["end"]

    cross_ref_errors = []

    try:
        for state in workflow_config.get("states", []) or []:
            state_id = state.get("id")

            _check_ref(state, 'assistant_id', assistant_ids, state_id, 'assistant', cross_ref_errors, 'assistant_id')
            _check_ref(state, 'tool_id', tool_ids, state_id, 'tool', cross_ref_errors, 'tool_id')
            _check_ref(
                state, 'custom_node_id', custom_node_ids, state_id, 'custom_node', cross_ref_errors, 'custom_node_id'
            )

            _handle_next_state(cross_ref_errors, state, state_id, state_ids)

    except AttributeError:
        return []

    return cross_ref_errors


def _handle_next_state(cross_ref_errors, state, state_id, state_ids):
    if "next" not in state:
        return

    next_config = state["next"]

    _check_ref(next_config, 'state_id', state_ids, state_id, 'state', cross_ref_errors, 'next.state_id')

    if "condition" in next_config:
        condition = next_config["condition"]
        _check_ref(condition, 'then', state_ids, state_id, 'state', cross_ref_errors, 'next.condition.then')
        _check_ref(
            condition,
            'otherwise',
            state_ids,
            state_id,
            'state',
            cross_ref_errors,
            'next.condition.otherwise',
        )

    if "switch" in next_config:
        switch_config = next_config["switch"]
        for i, case in enumerate(switch_config.get('cases', []) or []):
            _check_ref(
                case,
                'state_id',
                state_ids,
                state_id,
                'state',
                cross_ref_errors,
                f'next.switch.cases[{i}].state_id',
            )
        _check_ref(switch_config, 'default', state_ids, state_id, 'state', cross_ref_errors, 'next.switch.default')

    if next_config.get('iter_key') == '':
        cross_ref_errors.append(CrossRefError(state_id, 'next.iter_key', '', 'iter_key'))

    if "state_ids" in next_config:
        state_ids_config = next_config["state_ids"]
        for i, id in enumerate(state_ids_config):
            if id not in state_ids:
                cross_ref_errors.append(CrossRefError(state_id, f'next.state_ids[{i}]', id, 'state'))


def validate_workflow_execution_config_yaml(workflow_config_yaml: str):
    """
    Validates the execution workflow configuration provided in YAML format.

    This function parses the given YAML string into a dictionary and validates it
    against a predefined schema. If the YAML is malformed, a `WorkflowExecutionParsingError`
    is raised. If the parsed configuration does not conform to the expected schema,
    or contains cross-referenes issues, a `WorkflowExecutionConfigSchemaValidationError`
    or a `WorkflowExecutionConfigCrossReferenceValidationError` is raised,
    respectively

    Args:
        workflow_config_yaml (str): A YAML-formatted string representing the execution
                                    workflow configuration.

    Raises:
        WorkflowExecutionParsingError: If the input YAML string cannot be parsed.
        WorkflowExecutionConfigSchemaValidationError: If the parsed YAML does not conform
                                                      to the expected schema or contains
                                                      cross-references issues.
        WorkflowExecutionConfigCrossReferenceValidationError: If the parsed YAML contains
                                                      cross-references issues.
    """
    # Extract line numbers first
    line_number_map = extract_line_numbers(workflow_config_yaml)

    try:
        workflow_config = yaml.safe_load(workflow_config_yaml) or {}
    except yaml.YAMLError:
        raise WorkflowExecutionParsingError

    schema_validation_errors = _validate_workflow_execution_config_schema(
        WORKFLOW_EXECUTION_CONFIG_SCHEMA, workflow_config
    )

    if schema_validation_errors:
        raise WorkflowExecutionConfigSchemaValidationError(schema_validation_errors, workflow_config, line_number_map)

    cross_ref_errors = _validate_workflow_execution_config_cross_references(workflow_config)

    if cross_ref_errors:
        raise WorkflowExecutionConfigCrossReferenceValidationError(cross_ref_errors, line_number_map, workflow_config)
