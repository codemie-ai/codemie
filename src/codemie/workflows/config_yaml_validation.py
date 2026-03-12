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

from collections import deque, namedtuple
from typing import List
import yaml
from jsonschema import Draft202012Validator, ValidationError
from pathlib import Path
from codemie.workflows.constants import WorkflowErrorType, WorkflowValidationError


CrossRefError = namedtuple("CrossRefError", ["referrer", "key", "ref", "entity"])


class WorkflowExecutionConfigError(yaml.YAMLError):
    def __init__(self, message="YAML error occurred"):
        super().__init__(message)


class WorkflowExecutionParsingError(WorkflowExecutionConfigError):
    def __init__(self, message="Error occured while attempting to read YAML"):
        super().__init__(message)


class SchemaError(WorkflowExecutionConfigError):
    def __init__(self, message="Error occured while attempting to read and validate schema"):
        super().__init__(message)


class WorkflowExecutionConfigSchemaValidationError(WorkflowExecutionConfigError):
    def __init__(self, schema_validation_errors: List[ValidationError], workflow_config: dict = None):
        self.schema_validation_errors = schema_validation_errors
        self.workflow_config = workflow_config or {}
        messages = []
        for i, error in enumerate(schema_validation_errors):
            messages.append(
                f"{i + 1}) {WorkflowExecutionConfigSchemaValidationError._format_validation_error_message(error)}"
            )
        super().__init__(("\n").join(messages))

    def to_dict(self) -> dict:
        """Convert validation errors to structured dictionary format."""
        errors = []

        for error in self.schema_validation_errors:
            path = self._format_path(error.path, self.workflow_config)

            resource_type = None
            resource_id = None

            if path:
                parts = path.split('.')
                if len(parts) >= 2:
                    resource_type = parts[0].rstrip('s')  # Remove trailing 's' to get singular form
                    resource_id = parts[1]
                elif len(parts) == 1:
                    resource_type = parts[0]

            errors.append(
                WorkflowValidationError(
                    resource_type=resource_type,
                    resource_id=resource_id,
                    reference_state=None,
                    message=self._format_validation_error_message(error, include_path=False),
                ).model_dump(exclude_none=True)
            )

        return {"error_type": WorkflowErrorType.SCHEMA_VALIDATION.value, "errors": errors}

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
    def __init__(self, cross_ref_errors: List[CrossRefError]):
        self.cross_ref_errors = cross_ref_errors
        messages = []
        for i, error in enumerate(self.cross_ref_errors, start=len(messages) + 1):
            messages.append(WorkflowExecutionConfigCrossReferenceValidationError._format_cross_ref_error(i, error))
        super().__init__(("\n").join(messages))

    def to_dict(self) -> dict:
        """Convert cross-reference errors to structured dictionary format."""
        errors = []

        for error in self.cross_ref_errors:
            errors.append(
                WorkflowValidationError(
                    resource_type=error.entity,
                    resource_id=error.ref,
                    reference_state=error.referrer,
                    message=f"'{error.key}' key references undefined '{error.ref}' {error.entity}",
                ).model_dump()
            )

        return {"error_type": WorkflowErrorType.CROSS_REFERENCE_VALIDATION.value, "errors": errors}

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


WORKFLOW_EXECUTION_CONFIG_SCHEMA_YAML_FILE_PATH = Path(__file__).parent / "execution_config_schema.yaml"
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
    try:
        workflow_config = yaml.safe_load(workflow_config_yaml) or {}
    except yaml.YAMLError:
        raise WorkflowExecutionParsingError

    schema_validation_errors = _validate_workflow_execution_config_schema(
        WORKFLOW_EXECUTION_CONFIG_SCHEMA, workflow_config
    )

    if schema_validation_errors:
        raise WorkflowExecutionConfigSchemaValidationError(schema_validation_errors, workflow_config)

    cross_ref_errors = _validate_workflow_execution_config_cross_references(workflow_config)

    if cross_ref_errors:
        raise WorkflowExecutionConfigCrossReferenceValidationError(cross_ref_errors)
