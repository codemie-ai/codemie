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

import json
import re
from typing import Any, Optional, Type

from jinja2 import TemplateSyntaxError


from codemie.configs import logger
from codemie.core.template_security import TemplateSecurityError, render_secure_template
from codemie.core.thought_queue import ThoughtQueue
from codemie.core.workflow_models import CustomWorkflowNode, WorkflowState
from codemie.service.workflow_execution import WorkflowExecutionService
from codemie.workflows.callbacks.base_callback import BaseCallback
from codemie.workflows.constants import TASK_KEY
from codemie.workflows.models import AgentMessages
from codemie.workflows.nodes.base_node import BaseNode, StateSchemaType
from codemie.workflows.utils import DotDict, get_messages_from_state_schema
from codemie.workflows.utils.transform_node_utils import (
    extract_field,
    extract_from_context_store,
)

# Configuration keys
CONFIG_KEY_ON_ERROR = 'on_error'
CONFIG_KEY_MAPPINGS = 'mappings'
CONFIG_KEY_OUTPUT_SCHEMA = 'output_schema'
CONFIG_KEY_INPUT_SOURCE = 'input_source'
CONFIG_KEY_INPUT_KEY = 'input_key'
CONFIG_KEY_DEFAULT_OUTPUT = 'default_output'

# Error strategies
ERROR_STRATEGY_FAIL = 'fail'
ERROR_STRATEGY_SKIP = 'skip'
ERROR_STRATEGY_DEFAULT = 'default'
ERROR_STRATEGY_PARTIAL = 'partial'

# Input sources
INPUT_SOURCE_CONTEXT_STORE = 'context_store'
INPUT_SOURCE_MESSAGES = 'messages'
INPUT_SOURCE_USER_INPUT = 'user_input'
INPUT_SOURCE_STATE_SCHEMA = 'state_schema'
INPUT_SOURCE_COMBINED = 'combined'

# Mapping types
MAPPING_TYPE_EXTRACT = 'extract'
MAPPING_TYPE_CONDITION = 'condition'
MAPPING_TYPE_TEMPLATE = 'template'
MAPPING_TYPE_CONSTANT = 'constant'
MAPPING_TYPE_SCRIPT = 'script'
MAPPING_TYPE_ARRAY_MAP = 'array_map'

# Mapping field keys
MAPPING_KEY_OUTPUT_FIELD = 'output_field'
MAPPING_KEY_TYPE = 'type'
MAPPING_KEY_SOURCE_PATH = 'source_path'
MAPPING_KEY_DEFAULT = 'default'
MAPPING_KEY_CONDITION = 'condition'
MAPPING_KEY_THEN_VALUE = 'then_value'
MAPPING_KEY_ELSE_VALUE = 'else_value'
MAPPING_KEY_TEMPLATE = 'template'
MAPPING_KEY_VALUE = 'value'
MAPPING_KEY_SCRIPT = 'script'
MAPPING_KEY_ITEM_FIELD = 'item_field'
MAPPING_KEY_FILTER_CONDITION = 'filter_condition'

# Schema keys
SCHEMA_KEY_PROPERTIES = 'properties'
SCHEMA_KEY_REQUIRED = 'required'
SCHEMA_KEY_TYPE = 'type'

# Schema types
SCHEMA_TYPE_STRING = 'string'
SCHEMA_TYPE_INTEGER = 'integer'
SCHEMA_TYPE_BOOLEAN = 'boolean'
SCHEMA_TYPE_NUMBER = 'number'

# State schema keys
STATE_KEY_MESSAGES = 'messages'
STATE_KEY_CONTEXT_STORE = 'context_store'
STATE_KEY_NEXT = 'next'
STATE_KEY_USER_INPUT = 'user_input'

# Default values
DEFAULT_TASK_DESCRIPTION = "Transform data using configured mappings"
DEFAULT_DATA_WRAPPER_KEY = 'data'

# Dangerous patterns for safe eval
# Patterns that should always be blocked regardless of context
DANGEROUS_PATTERNS_STRICT = [
    r'\b__import__\b',  # Import statements
    r'\beval\b',  # Eval function
    r'\bexec\b',  # Exec function
    r'\bcompile\b',  # Compile function
    r'\binput\b',  # Input function
    r'__\w+__',  # Dunder methods (except those in safe globals)
]

# Patterns that should only be blocked when used as function calls (followed by parenthesis)
# This allows these words as string literals but blocks function calls
DANGEROUS_PATTERNS_FUNCTION_CALLS = [
    r'\bopen\s*\(',  # open() function call
    r'\bfile\s*\(',  # file() function call
]


class TransformationError(Exception):
    """Raised when transformation fails"""

    pass


class TransformNode(BaseNode[AgentMessages]):
    """
    Transform node for data manipulation and mapping in workflows.

    Supports:
    - JSON field mapping with JSONPath/dot notation
    - Conditional logic and expressions
    - Jinja2 templates for complex transformations
    - Type coercion and validation
    """

    # Safe namespace for eval() to prevent code injection
    SAFE_GLOBALS = {
        '__builtins__': {
            'True': True,
            'False': False,
            'None': None,
            'str': str,
            'int': int,
            'float': float,
            'bool': bool,
            'len': len,
            'min': min,
            'max': max,
            'sum': sum,
            'abs': abs,
            'list': list,
            'dict': dict,
            'set': set,
            'tuple': tuple,
            'any': any,
            'all': all,
            'isinstance': isinstance,
        }
    }

    def __init__(
        self,
        callbacks: list[BaseCallback],
        workflow_execution_service: WorkflowExecutionService,
        thought_queue: ThoughtQueue,
        workflow_state: WorkflowState,
        custom_node: CustomWorkflowNode,
        execution_id: Optional[str] = None,
        *args,
        **kwargs,
    ):
        """Initialize TransformNode with configuration.

        Args:
            callbacks: List of callback handlers for node lifecycle events
            workflow_execution_service: Service for managing workflow execution states
            thought_queue: Queue for managing AI agent thoughts and reasoning
            workflow_state: Configuration and state information for the workflow
            custom_node: Custom node configuration containing transform rules
            execution_id: Optional unique identifier for the current execution
            *args: Additional positional arguments
            **kwargs: Additional keyword arguments
        """
        super().__init__(
            callbacks,
            workflow_execution_service,
            thought_queue,
            *args,
            execution_id=execution_id,
            workflow_state=workflow_state,
            **kwargs,
        )
        self.custom_node = custom_node

    def execute(self, state_schema: AgentMessages, execution_context: dict) -> dict:
        """Execute transformation logic.

        Steps:
        1. Extract source data from state_schema and context_store
        2. Apply field mappings
        3. Evaluate conditions
        4. Execute Jinja2 templates if configured
        5. Return transformed output as dict

        Args:
            state_schema: The current state schema containing workflow data and messages
            execution_context: Dictionary containing execution context variables and services

        Returns:
            dict: Transformed output data

        Raises:
            TransformationError: If transformation fails and on_error is 'fail'
        """
        config = self.custom_node.config
        on_error = config.get(CONFIG_KEY_ON_ERROR, ERROR_STRATEGY_FAIL)

        try:
            # Extract source data based on configuration
            source_data = self._extract_source_data(state_schema)

            logger.debug(
                "TransformNode: Extracted source data",
                extra={'node_id': self.custom_node.id, 'source_data_keys': list(source_data.keys())},
            )

            # Apply mappings to transform data
            mappings = config.get(CONFIG_KEY_MAPPINGS, [])
            output = self._apply_mappings(source_data, mappings)

            logger.debug(
                "TransformNode: Applied mappings",
                extra={'node_id': self.custom_node.id, 'output_keys': list(output.keys())},
            )

            # Validate output if schema provided
            output_schema = config.get(CONFIG_KEY_OUTPUT_SCHEMA)
            if output_schema:
                output = self._validate_output(output, output_schema)

            return output

        except Exception as e:
            logger.error(
                f"TransformNode: Transformation failed: {str(e)}",
                extra={'node_id': self.custom_node.id, 'error_strategy': on_error},
                exc_info=True,
            )

            if on_error == ERROR_STRATEGY_FAIL:
                raise TransformationError(f"Transformation failed: {str(e)}") from e
            elif on_error == ERROR_STRATEGY_SKIP:
                logger.warning("TransformNode: Skipping transformation due to error")
                return {}
            elif on_error == ERROR_STRATEGY_DEFAULT:
                default_output = config.get(CONFIG_KEY_DEFAULT_OUTPUT, {})
                logger.warning("TransformNode: Returning default output due to error")
                return default_output
            else:
                # Unknown error strategy, fail safely
                raise TransformationError(f"Unknown error strategy: {on_error}") from e

    def _extract_source_data(self, state_schema: AgentMessages) -> dict:
        """Extract data based on input_source configuration.

        Args:
            state_schema: The current state schema

        Returns:
            dict: Extracted source data
        """
        config = self.custom_node.config
        input_source = config.get(CONFIG_KEY_INPUT_SOURCE, INPUT_SOURCE_CONTEXT_STORE)
        input_key = config.get(CONFIG_KEY_INPUT_KEY)

        source_data = {}

        if input_source == INPUT_SOURCE_CONTEXT_STORE:
            source_data = self._extract_from_context_store(state_schema, input_key, source_data)
        elif input_source == INPUT_SOURCE_MESSAGES:
            source_data = self._extract_from_messages(state_schema, source_data)
        elif input_source == INPUT_SOURCE_USER_INPUT:
            source_data = self._extract_from_user_input(state_schema, source_data)
        elif input_source == INPUT_SOURCE_STATE_SCHEMA:
            source_data = self._extract_from_state_schema(state_schema, source_data)

        # If source_data is not a dict at this point and we have input_source != combined
        if not isinstance(source_data, dict) and input_source != INPUT_SOURCE_COMBINED:
            source_data = {DEFAULT_DATA_WRAPPER_KEY: source_data}

        return source_data

    def _extract_from_messages(self, state_schema: AgentMessages, source_data: dict) -> dict:
        """Extract data from messages."""
        messages = get_messages_from_state_schema(state_schema)
        if messages:
            last_message = messages[-1]
            if hasattr(last_message, 'content'):
                content = last_message.content
                if isinstance(content, str):
                    try:
                        parsed = json.loads(content)
                        if isinstance(parsed, dict):
                            source_data.update(parsed)
                    except json.JSONDecodeError:
                        pass
        return source_data

    def _extract_from_user_input(self, state_schema: AgentMessages, source_data: dict) -> dict:
        """Extract data from user input."""
        user_input = state_schema.get(STATE_KEY_USER_INPUT, '')
        if user_input:
            try:
                parsed = json.loads(user_input)
                if isinstance(parsed, dict):
                    source_data.update(parsed)
            except json.JSONDecodeError:
                pass
        return source_data

    def _extract_from_state_schema(self, state_schema: AgentMessages, source_data: dict) -> dict:
        """Extract data from state schema."""
        excluded_keys = {STATE_KEY_MESSAGES, STATE_KEY_CONTEXT_STORE, STATE_KEY_NEXT}
        for key, value in state_schema.items():
            if key not in excluded_keys:
                source_data[key] = value
        return source_data

    def _apply_mappings(self, source_data: dict, mappings: list) -> dict:
        """Apply all configured mappings.

        Mappings are applied sequentially, and each mapping can reference
        both the original source_data and previously computed output fields.

        Args:
            source_data: Source data to transform
            mappings: List of mapping configurations

        Returns:
            dict: Transformed output
        """
        output = {}

        for mapping in mappings:
            try:
                # Create combined context: source_data + previously computed outputs
                # This allows later mappings to reference earlier ones
                combined_context = {**source_data, **output}

                output_field, value = self._apply_mapping(combined_context, mapping, output)
                output[output_field] = value
            except Exception as e:
                output_field = mapping.get(MAPPING_KEY_OUTPUT_FIELD)
                logger.warning(
                    f"TransformNode: Failed to apply mapping for field '{output_field}': {str(e)}",
                    extra={'mapping': mapping},
                )
                # Check if we should include partial results
                on_error = self.custom_node.config.get(CONFIG_KEY_ON_ERROR, ERROR_STRATEGY_FAIL)
                if on_error == ERROR_STRATEGY_PARTIAL:
                    # Continue with other mappings
                    continue
                else:
                    raise

        return output

    def _apply_mapping(self, source_data: dict, mapping: dict, output: dict = None) -> tuple[str, Any]:
        """Apply single mapping based on type.

        Args:
            source_data: Source data to transform (includes previously computed outputs)
            mapping: Mapping configuration
            output: Previously computed output fields (for reference)

        Returns:
            tuple: (output_field, transformed_value)
        """
        output_field = mapping.get(MAPPING_KEY_OUTPUT_FIELD)
        if not output_field:
            raise TransformationError(f"Mapping must have '{MAPPING_KEY_OUTPUT_FIELD}'")

        mapping_type = mapping.get(MAPPING_KEY_TYPE, MAPPING_TYPE_EXTRACT)

        if mapping_type == MAPPING_TYPE_EXTRACT:
            value = self._extract_field(
                source_data, mapping.get(MAPPING_KEY_SOURCE_PATH, ''), mapping.get(MAPPING_KEY_DEFAULT)
            )
        elif mapping_type == MAPPING_TYPE_CONDITION:
            value = self._evaluate_condition(
                source_data,
                mapping.get(MAPPING_KEY_CONDITION, ''),
                mapping.get(MAPPING_KEY_THEN_VALUE),
                mapping.get(MAPPING_KEY_ELSE_VALUE),
            )
        elif mapping_type == MAPPING_TYPE_TEMPLATE:
            value = self._render_template(source_data, mapping.get(MAPPING_KEY_TEMPLATE, ''))
        elif mapping_type == MAPPING_TYPE_CONSTANT:
            value = mapping.get(MAPPING_KEY_VALUE)
        elif mapping_type == MAPPING_TYPE_SCRIPT:
            value = self._evaluate_script(source_data, mapping.get(MAPPING_KEY_SCRIPT, ''))
        elif mapping_type == MAPPING_TYPE_ARRAY_MAP:
            value = self._map_array(source_data, mapping)
        else:
            raise TransformationError(f"Unknown mapping type: {mapping_type}")

        return output_field, value

    def _evaluate_condition(self, source_data: dict, condition: str, then_value: Any, else_value: Any) -> Any:
        """Evaluate conditional expression.

        Args:
            source_data: Source data for variable resolution
            condition: Boolean expression to evaluate
            then_value: Value to return if condition is True
            else_value: Value to return if condition is False

        Returns:
            Any: then_value or else_value based on condition
        """
        if not condition:
            return else_value

        try:
            # Create local variables from source_data
            # Wrap nested dicts to support dot notation (e.g., pull_request.id)
            local_vars = {}
            for key, value in source_data.items():
                if isinstance(value, dict):
                    # Wrap dicts to allow dot notation access
                    local_vars[key] = DotDict(value)
                else:
                    local_vars[key] = value

            # Safely evaluate condition
            result = self._safe_eval(condition, local_vars)

            return then_value if result else else_value
        except Exception as e:
            logger.warning(f"TransformNode: Failed to evaluate condition '{condition}': {str(e)}")
            return else_value

    def _safe_eval(self, expr: str, local_vars: dict) -> Any:
        """Safely evaluate expression with restricted namespace.

        Args:
            expr: Expression to evaluate
            local_vars: Local variables for evaluation

        Returns:
            Any: Evaluation result

        Raises:
            TransformationError: If evaluation fails or uses unsafe constructs
        """
        # Remove string literals from expression before checking for dangerous patterns
        # This allows keywords like 'open', 'close' as string values but blocks them as code
        expr_without_strings = self._remove_string_literals(expr)

        # Check for patterns that should always be blocked
        for pattern in DANGEROUS_PATTERNS_STRICT:
            if re.search(pattern, expr_without_strings):
                raise TransformationError(
                    f"Expression contains potentially dangerous construct matching pattern: {pattern}"
                )

        # Check for function call patterns (these check the original expression)
        for pattern in DANGEROUS_PATTERNS_FUNCTION_CALLS:
            if re.search(pattern, expr):
                raise TransformationError(
                    f"Expression contains potentially dangerous function call matching pattern: {pattern}"
                )

        try:
            return eval(expr, self.SAFE_GLOBALS, local_vars)
        except Exception as e:
            raise TransformationError(f"Failed to evaluate expression '{expr}': {str(e)}") from e

    def _remove_string_literals(self, expr: str) -> str:
        """Remove string literals from expression to avoid false positives in security checks.

        This allows checking for dangerous patterns in code while ignoring string contents.
        For example: status == 'open' -> status == ''

        Args:
            expr: Expression to process

        Returns:
            str: Expression with string literals replaced by empty strings
        """
        # Remove single-quoted strings
        expr = re.sub(r"'[^']*'", "''", expr)
        # Remove double-quoted strings
        expr = re.sub(r'"[^"]*"', '""', expr)
        return expr

    def _render_template(self, source_data: dict, template: str) -> str:
        """Render Jinja2 template in a restricted sandbox to prevent SSTI/RCE.

        Supported: variable substitution ({{ var }}), conditionals ({% if %}),
        loops ({% for %}), and standard filters (|length, |upper, |join, etc.).

        Blocked: access to Python internals (__class__, __import__, etc.),
        OS/subprocess calls (os., open(), popen()), and private attributes (_x).
        Violations raise TransformationError("Template security violation: ...").

        Output is not HTML-escaped — transform output is plain workflow data.

        Args:
            source_data: Data available as template variables
            template: Jinja2 template string

        Returns:
            str: Rendered template

        Raises:
            TransformationError: If rendering fails or a security violation is detected
        """
        if not template:
            return ""

        try:
            return render_secure_template(template, source_data, autoescape=False)
        except TemplateSecurityError as e:
            raise TransformationError(f"Template security violation: {str(e)}") from e
        except TemplateSyntaxError as e:
            raise TransformationError(f"Template syntax error: {str(e)}") from e
        except Exception as e:
            raise TransformationError(f"Template rendering failed: {str(e)}") from e

    def _evaluate_script(self, source_data: dict, script: str) -> Any:
        """Execute Python expression for complex logic.

        Args:
            source_data: Source data for variable resolution
            script: Python expression to execute

        Returns:
            Any: Script execution result
        """
        if not script:
            return None

        try:
            # Create local variables from source_data
            # Wrap nested dicts to support dot notation (e.g., pull_request.id)
            local_vars = {}
            for key, value in source_data.items():
                if isinstance(value, dict):
                    # Wrap dicts to allow dot notation access
                    local_vars[key] = DotDict(value)
                else:
                    local_vars[key] = value

            # Safely evaluate script
            result = self._safe_eval(script, local_vars)

            return result
        except Exception as e:
            raise TransformationError(f"Script execution failed: {str(e)}") from e

    def _map_array(self, source_data: dict, mapping: dict) -> list:
        """Map array items to extract specific fields.

        Useful for extracting fields from array of objects, like:
        labels: [{name: "WS"}, {name: "bug"}] -> ["WS", "bug"]

        Args:
            source_data: Source data dictionary
            mapping: Mapping configuration with:
                - source_path: Path to array
                - item_field: Field to extract from each item (optional)
                - filter_condition: Optional condition to filter items (optional)

        Returns:
            list: Mapped array values
        """
        source_path = mapping.get(MAPPING_KEY_SOURCE_PATH, '')
        item_field = mapping.get(MAPPING_KEY_ITEM_FIELD)
        filter_condition = mapping.get(MAPPING_KEY_FILTER_CONDITION)

        array = self._extract_field(source_data, source_path, [])

        if not isinstance(array, list):
            logger.warning(f"TransformNode: array_map expected list at '{source_path}', got {type(array).__name__}")
            return []

        try:
            result = []
            for item in array:
                if self._should_filter_item(item, filter_condition, source_data):
                    continue

                mapped_value = self._extract_item_value(item, item_field)
                if mapped_value is not None:
                    result.append(mapped_value)

            return result
        except Exception as e:
            raise TransformationError(f"Array mapping failed: {str(e)}") from e

    def _should_filter_item(self, item: Any, filter_condition: Optional[str], source_data: dict) -> bool:
        """Check if an item should be filtered out based on condition.

        Args:
            item: Item to check
            filter_condition: Condition expression to evaluate
            source_data: Source data for variable resolution

        Returns:
            bool: True if item should be filtered out, False otherwise
        """
        if not filter_condition:
            return False

        local_vars = {'item': item}
        local_vars.update(source_data)
        try:
            return not self._safe_eval(filter_condition, local_vars)
        except Exception as e:
            logger.warning(f"TransformNode: Filter condition failed for item: {e}")
            return True

    def _extract_item_value(self, item: Any, item_field: Optional[str]) -> Any:
        """Extract value from an array item.

        Args:
            item: Item to extract value from
            item_field: Field name to extract (None to use whole item)

        Returns:
            Any: Extracted value or None if extraction failed
        """
        if not item_field:
            return item

        if isinstance(item, dict):
            return item.get(item_field)

        # Try direct attribute access for objects
        try:
            return getattr(item, item_field, None)
        except (AttributeError, TypeError):
            return None

    def _validate_output(self, output: dict, schema: dict) -> dict:
        """Validate output against JSON schema.

        Args:
            output: Output data to validate
            schema: JSON schema definition (simplified Pydantic-compatible)

        Returns:
            dict: Validated output (potentially coerced types)

        Raises:
            TransformationError: If validation fails
        """
        try:
            properties = schema.get(SCHEMA_KEY_PROPERTIES, {})
            required = schema.get(SCHEMA_KEY_REQUIRED, [])

            self._check_required_fields(output, required)
            validated_output = self._coerce_output_types(output, properties)

            return validated_output

        except (ValueError, TypeError) as e:
            raise TransformationError(f"Output validation failed: {str(e)}") from e

    def _check_required_fields(self, output: dict, required: list) -> None:
        """Check if all required fields are present in output.

        Args:
            output: Output data to check
            required: List of required field names

        Raises:
            TransformationError: If a required field is missing
        """
        for field in required:
            if field not in output:
                raise TransformationError(f"Required field missing: {field}")

    def _coerce_output_types(self, output: dict, properties: dict) -> dict:
        """Coerce output types based on schema properties.

        Args:
            output: Output data to coerce
            properties: Schema properties defining expected types

        Returns:
            dict: Output with coerced types
        """
        validated_output = {}
        for field, value in output.items():
            if field in properties:
                field_schema = properties[field]
                validated_output[field] = self._coerce_field_type(value, field_schema)
            else:
                validated_output[field] = value
        return validated_output

    def _coerce_field_type(self, value: Any, field_schema: dict) -> Any:
        """Coerce a single field value to the expected type.

        Args:
            value: Value to coerce
            field_schema: Schema definition for the field

        Returns:
            Any: Coerced value
        """
        field_type = field_schema.get(SCHEMA_KEY_TYPE)

        if field_type == SCHEMA_TYPE_STRING and not isinstance(value, str):
            return str(value)
        elif field_type == SCHEMA_TYPE_INTEGER and not isinstance(value, int):
            return int(value)
        elif field_type == SCHEMA_TYPE_BOOLEAN and not isinstance(value, bool):
            return bool(value)
        elif field_type == SCHEMA_TYPE_NUMBER and not isinstance(value, (int, float)):
            return float(value)
        else:
            return value

    def _extract_from_context_store(
        self, state_schema: AgentMessages, input_key: Optional[str], source_data: dict
    ) -> dict:
        """Instance method wrapper for extract_from_context_store utility.

        This allows the method to access self if needed for future enhancements.

        Args:
            state_schema: The current state schema containing context_store
            input_key: Key or nested path to extract
            source_data: Dictionary to populate with extracted data

        Returns:
            dict: Source data with extracted values
        """
        return extract_from_context_store(state_schema, input_key, source_data)

    def _extract_field(self, source_data: dict, source_path: str, default=None) -> Any:
        """Instance method wrapper for extract_field utility.

        This allows the method to access self if needed for future enhancements.

        Args:
            source_data: Source data dictionary
            source_path: Path to field (dot notation or JSONPath)
            default: Default value if field not found

        Returns:
            Any: Extracted value or default
        """
        return extract_field(source_data, source_path, default)

    def get_task(self, state_schema: AgentMessages, *arg, **kwargs) -> str:
        """Get the task description for this node.

        Args:
            state_schema: The current state schema containing workflow data and messages
            *arg: Additional positional arguments
            **kwargs: Additional keyword arguments

        Returns:
            str: A human-readable description of the task being performed
        """
        task = self.workflow_state.task or DEFAULT_TASK_DESCRIPTION
        return task

    def post_process_output(self, state_schema: Type[StateSchemaType], task, output: dict) -> str:
        """Process the transformation output into a string format.

        Args:
            state_schema: The current state schema
            task: The task description that was executed
            output: The transformed output dictionary

        Returns:
            str: JSON string representation of the output
        """
        return json.dumps(output, ensure_ascii=False)

    def _add_iteration_state(
        self, final_state: dict[str, Any], state_schema: Type[StateSchemaType], processed_output: str
    ) -> None:
        """Add iteration-specific state for TransformNode.

        This override ensures that transform nodes properly preserve iteration context
        when chained within an iteration (e.g., agent -> transform -> transform -> end).
        Without this, the iteration chain would break because the iteration task data
        wouldn't be passed forward to the next node.

        Args:
            final_state: The final state dictionary to update
            state_schema: The current state schema
            processed_output: The processed output string
        """
        # Call parent implementation to set ITER_SOURCE, ITERATION_NODE_NUMBER_KEY, TOTAL_ITERATIONS_KEY
        super()._add_iteration_state(final_state, state_schema, processed_output)

        # If no workflow_state or no iter_key, parent already returned early
        if not self.workflow_state or not self.workflow_state.next.iter_key:
            return

        state_next = self.workflow_state.next

        # Preserve iteration task data to allow continuation to the next node in the iteration chain
        # This is critical for transform nodes which don't have current_task_key like agent nodes do
        if state_schema.get(TASK_KEY) is not None:
            final_state[state_next.iter_key] = (
                processed_output if state_next.override_task else state_schema.get(TASK_KEY)
            )
