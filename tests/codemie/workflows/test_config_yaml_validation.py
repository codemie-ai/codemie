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

import pytest
from unittest.mock import mock_open, patch
import yaml
from jsonschema import Draft202012Validator

from codemie.workflows.validation.schema import (
    _extract_ids,
    _load_schema_from_file,
    _validate_workflow_execution_config_schema,
    _validate_workflow_execution_config_cross_references,
    validate_workflow_execution_config_yaml,
    WorkflowExecutionParsingError,
    SchemaError,
    WorkflowExecutionConfigSchemaValidationError,
    WorkflowExecutionConfigCrossReferenceValidationError,
    WORKFLOW_EXECUTION_CONFIG_SCHEMA,
)
from codemie.workflows.validation.models import CrossRefError
from codemie.workflows.validation.line_lookup import (
    extract_line_numbers,
    YamlLineFinder,
    _convert_path_to_string,
)


@pytest.mark.parametrize(
    "workflow_config, key, expected",
    [
        ({}, "assistants", []),
        ({"assistants": None}, "assistants", []),
        ({"assistants": []}, "assistants", []),
        ({"assistants": [{"id": None}]}, "assistants", []),
        ({"assistants": [{"id": ""}]}, "assistants", []),
        ({"assistants": [{"id": "id_1"}]}, "assistants", ["id_1"]),
        ({"assistants": [None, {"id": "id_10"}]}, "assistants", ["id_10"]),
        ({"assistants": [{"id": None}, {"id": "id_10"}]}, "assistants", ["id_10"]),
        ({"assistants": [{"id": "id_10"}, {"id": "id_100"}]}, "assistants", ["id_10", "id_100"]),
        ({"assistants": [{}, {"id": "id_10"}]}, "assistants", ["id_10"]),
    ],
)
def test_extract_ids(workflow_config, key, expected):
    assert _extract_ids(workflow_config, key) == expected


@pytest.mark.parametrize(
    "file_content, schema_validation, expected",
    [
        ("{'key': 'value'}", False, {"key": "value"}),
        ("{'invalid': 'schema'}", True, pytest.raises(SchemaError)),
        ("invalid_yaml: [unclosed", False, pytest.raises(SchemaError)),
    ],
)
def test_load_schema_from_file(file_content, schema_validation, expected):
    mock_open_file = mock_open(read_data=file_content)
    with patch("builtins.open", mock_open_file):
        with patch.object(Draft202012Validator, "check_schema", return_value=schema_validation):
            if isinstance(expected, dict):
                assert _load_schema_from_file("dummy_path") == expected
            else:
                with expected:
                    _load_schema_from_file("dummy_path")


def test_load_schema_from_file_not_found():
    with pytest.raises(SchemaError):
        _load_schema_from_file("non_existent_file.yaml")


valid_yaml = """
assistants:
  - id: assistant_1
    assistant_id: uuid
states:
  - id: state_1
    assistant_id: assistant_1
    next:
      state_id: state_1
"""

invalid_schema_yaml = """
assistants:
  - id: assistant_1
    assistant_id: uuid
states:
  - id: state_1
"""

invalid_cross_ref_yaml = """
assistants: []
tools: []
custom_nodes: []
states:
  - id: state_1
    assistant_id: unknown_assistant
    next:
      state_id: end
"""


invalid_format_yaml = "::: this is not yaml :::"


def test_valid_schema():
    workflow_config = yaml.safe_load(valid_yaml)
    assert _validate_workflow_execution_config_schema(WORKFLOW_EXECUTION_CONFIG_SCHEMA, workflow_config) == []


def test_invalid_schema():
    workflow_config = yaml.safe_load(invalid_schema_yaml)
    assert len(_validate_workflow_execution_config_schema(WORKFLOW_EXECUTION_CONFIG_SCHEMA, workflow_config)) > 0


def test_valid_cross_references():
    workflow_config = yaml.safe_load(valid_yaml)
    assert _validate_workflow_execution_config_cross_references(workflow_config) == []


def test_invalid_cross_reference_assistant():
    workflow_config = yaml.safe_load(invalid_cross_ref_yaml)
    errors = _validate_workflow_execution_config_cross_references(workflow_config)
    assert len(errors) == 1
    assert isinstance(errors[0], CrossRefError)
    assert errors[0].key == "assistant_id"
    assert errors[0].ref == "unknown_assistant"


def test_validate_workflow_execution_config_yaml_success():
    validate_workflow_execution_config_yaml(valid_yaml)


def test_validate_workflow_execution_config_yaml_schema_error():
    with pytest.raises(WorkflowExecutionConfigSchemaValidationError) as exc_info:
        validate_workflow_execution_config_yaml(invalid_schema_yaml)
    assert len(exc_info.value.schema_validation_errors) > 0


def test_validate_workflow_execution_config_yaml_cross_ref_error():
    with pytest.raises(WorkflowExecutionConfigCrossReferenceValidationError) as exc_info:
        validate_workflow_execution_config_yaml(invalid_cross_ref_yaml)
    assert isinstance(exc_info.value.cross_ref_errors[0], CrossRefError)


def test_validate_workflow_execution_config_yaml_parse_error():
    with pytest.raises(WorkflowExecutionParsingError):
        validate_workflow_execution_config_yaml(invalid_format_yaml)


# Tests for iter_key validation
invalid_iter_key_with_condition_yaml = """
assistants:
  - id: assistant_1
    assistant_id: uuid
states:
  - id: state_1
    assistant_id: assistant_1
    next:
      state_id: state_2
      iter_key: items
      condition:
        expression: "status == 'success'"
        then: then_state
        otherwise: otherwise_state
  - id: state_2
    assistant_id: assistant_1
    next:
      state_id: end
  - id: then_state
    assistant_id: assistant_1
    next:
      state_id: end
  - id: otherwise_state
    assistant_id: assistant_1
    next:
      state_id: end
"""

invalid_iter_key_with_switch_yaml = """
assistants:
  - id: assistant_1
    assistant_id: uuid
states:
  - id: state_1
    assistant_id: assistant_1
    next:
      state_id: state_2
      iter_key: items
      switch:
        cases:
          - condition: "status == 'success'"
            state_id: success_state
        default: default_state
  - id: state_2
    assistant_id: assistant_1
    next:
      state_id: end
  - id: success_state
    assistant_id: assistant_1
    next:
      state_id: end
  - id: default_state
    assistant_id: assistant_1
    next:
      state_id: end
"""

valid_iter_key_with_state_id_yaml = """
assistants:
  - id: assistant_1
    assistant_id: uuid
states:
  - id: state_1
    assistant_id: assistant_1
    next:
      state_id: state_2
      iter_key: items
  - id: state_2
    assistant_id: assistant_1
    next:
      state_id: end
"""


def test_iter_key_with_condition_raises_error():
    """Test that iter_key combined with condition raises validation error."""
    with pytest.raises(WorkflowExecutionConfigSchemaValidationError) as exc_info:
        validate_workflow_execution_config_yaml(invalid_iter_key_with_condition_yaml)

    error_message = str(exc_info.value)
    assert "iter_key" in error_message.lower() or "condition" in error_message.lower()


def test_iter_key_with_switch_raises_error():
    """Test that iter_key combined with switch raises validation error."""
    with pytest.raises(WorkflowExecutionConfigSchemaValidationError) as exc_info:
        validate_workflow_execution_config_yaml(invalid_iter_key_with_switch_yaml)

    error_message = str(exc_info.value)
    assert "iter_key" in error_message.lower() or "switch" in error_message.lower()


def test_iter_key_with_state_id_succeeds():
    """Test that iter_key combined with state_id is valid."""
    # Should not raise any exception
    validate_workflow_execution_config_yaml(valid_iter_key_with_state_id_yaml)


def test_schema_validation_error_to_dict():
    workflow_config = yaml.safe_load(invalid_schema_yaml)
    errors = _validate_workflow_execution_config_schema(WORKFLOW_EXECUTION_CONFIG_SCHEMA, workflow_config)

    exception = WorkflowExecutionConfigSchemaValidationError(errors, workflow_config)
    error_dict = exception.to_dict()

    assert error_dict["error_type"] == "schema_validation"
    assert error_dict["message"] == "Configuration contains validation errors"
    assert "errors" in error_dict
    assert len(error_dict["errors"]) > 0

    # Verify new format structure
    for error in error_dict["errors"]:
        assert "id" in error  # UUID
        assert "message" in error  # Short message
        assert "path" in error  # Field path
        # Optional fields: details, state_id, config_line


def test_cross_reference_validation_error_to_dict():
    workflow_config = yaml.safe_load(invalid_cross_ref_yaml)
    errors = _validate_workflow_execution_config_cross_references(workflow_config)

    exception = WorkflowExecutionConfigCrossReferenceValidationError(errors)
    error_dict = exception.to_dict()

    assert error_dict["error_type"] == "cross_reference_validation"
    assert error_dict["message"] == "Configuration contains cross-reference errors"
    assert "errors" in error_dict
    assert len(error_dict["errors"]) == 1

    # Verify new format structure
    error = error_dict["errors"][0]
    assert "id" in error  # UUID
    assert error["message"] == "Invalid reference"
    assert "details" in error
    assert "unknown_assistant" in error["details"]  # Check details instead of message
    assert error["state_id"] == "state_1"
    assert "path" in error
    # config_line is optional


# ============================================================================
# NEW TESTS: Line Number Extraction and Centralized Logic
# ============================================================================


def testextract_line_numbers_simple_yaml():
    """Test line number extraction from simple YAML."""
    yaml_text = """max_concurrency: 5
messages_limit_before_summarization: 10
states:
  - id: assistant_1
    model: gpt-4
"""
    line_map = extract_line_numbers(yaml_text)

    assert line_map["max_concurrency"] == 1
    assert line_map["messages_limit_before_summarization"] == 2
    assert line_map["states[0].id"] == 4
    assert line_map["states[0].model"] == 5


def testextract_line_numbers_nested_yaml():
    """Test line number extraction from nested YAML structures."""
    yaml_text = """max_concurrency: 5
states:
  - id: assistant_1
    model: gpt-4
    next:
      state_id: end
"""
    line_map = extract_line_numbers(yaml_text)

    assert line_map["max_concurrency"] == 1
    assert line_map["states[0].id"] == 3
    assert line_map["states[0].model"] == 4
    assert line_map["states[0].next.state_id"] == 6


def testextract_line_numbers_multiple_states():
    """Test line number extraction with multiple states."""
    yaml_text = """states:
  - id: state_1
    assistant_id: asst_1
    model: gpt-4
  - id: state_2
    assistant_id: asst_2
    model: gpt-3.5
"""
    line_map = extract_line_numbers(yaml_text)

    assert line_map["states[0].id"] == 2
    assert line_map["states[0].assistant_id"] == 3
    assert line_map["states[0].model"] == 4
    assert line_map["states[1].id"] == 5
    assert line_map["states[1].assistant_id"] == 6
    assert line_map["states[1].model"] == 7


def testextract_line_numbers_empty_yaml():
    """Test line number extraction with empty YAML."""
    line_map = extract_line_numbers("")
    assert line_map == {}


def testextract_line_numbers_invalid_yaml():
    """Test line number extraction with invalid YAML."""
    yaml_text = "invalid: [unclosed"
    line_map = extract_line_numbers(yaml_text)
    assert line_map == {}


def test_convert_path_to_string_simple():
    """Test path conversion for simple paths."""
    assert _convert_path_to_string(["max_concurrency"]) == "max_concurrency"
    assert _convert_path_to_string(["states"]) == "states"


def test_convert_path_to_string_with_array_index():
    """Test path conversion with array indices."""
    assert _convert_path_to_string(["states", 0, "model"]) == "states[0].model"
    assert _convert_path_to_string(["states", 1, "id"]) == "states[1].id"


def test_convert_path_to_string_nested():
    """Test path conversion for nested structures."""
    assert _convert_path_to_string(["states", 0, "next", "state_id"]) == "states[0].next.state_id"
    assert _convert_path_to_string(["retry_policy", "max_attempts"]) == "retry_policy.max_attempts"


def test_find_line_number_top_level_field():
    """Test finding line number for top-level field."""
    workflow_config = {}
    line_number_map = {"max_concurrency": 1}

    finder = YamlLineFinder(workflow_config, line_number_map)
    line_num = finder.find_line_for_top_level_field("max_concurrency")

    assert line_num == 1


def test_find_line_number_state_specific_field():
    """Test finding line number for state-specific field."""
    workflow_config = {"states": [{"id": "assistant_1", "model": "gpt-4"}]}

    line_number_map = {"states[0].id": 3, "states[0].model": 4}

    finder = YamlLineFinder(workflow_config, line_number_map)
    line_num = finder.find_line_for_state_field("assistant_1", "model")

    assert line_num == 4


def test_find_line_number_nested_field():
    """Test finding line number for nested field in state."""
    workflow_config = {"states": [{"id": "state_1", "next": {"state_id": "end"}}]}

    line_number_map = {"states[0].next.state_id": 6}

    finder = YamlLineFinder(workflow_config, line_number_map)
    line_num = finder.find_line_for_state_field("state_1", "next.state_id")

    assert line_num == 6


def test_find_line_number_multiple_states():
    """Test finding line number with multiple states."""
    workflow_config = {"states": [{"id": "state_1", "model": "gpt-4"}, {"id": "state_2", "model": "gpt-3.5"}]}

    line_number_map = {"states[0].model": 4, "states[1].model": 7}

    finder = YamlLineFinder(workflow_config, line_number_map)

    # Find line for first state
    line_num_1 = finder.find_line_for_state_field("state_1", "model")
    assert line_num_1 == 4

    # Find line for second state
    line_num_2 = finder.find_line_for_state_field("state_2", "model")
    assert line_num_2 == 7


def test_find_line_number_state_not_found():
    """Test finding line number when state doesn't exist."""
    workflow_config = {"states": [{"id": "state_1"}]}
    line_number_map = {"states[0].model": 4}

    finder = YamlLineFinder(workflow_config, line_number_map)
    line_num = finder.find_line_for_state_field("nonexistent", "model")

    assert line_num is None


def test_find_line_number_field_not_in_map():
    """Test finding line number when field is not in map."""
    workflow_config = {"states": [{"id": "state_1"}]}
    line_number_map = {}

    finder = YamlLineFinder(workflow_config, line_number_map)
    line_num = finder.find_line_for_state_field("state_1", "model")

    assert line_num is None


def test_find_line_number_empty_map():
    """Test finding line number with empty line number map."""
    workflow_config = {"states": [{"id": "state_1"}]}

    finder = YamlLineFinder(workflow_config, {})
    line_num = finder.find_line_for_state_field("state_1", "model")

    assert line_num is None


def test_schema_validation_error_with_line_numbers():
    """Test schema validation error includes line numbers."""
    yaml_text = """max_concurrency: "invalid"
states:
  - id: assistant_1
    assistant_id: asst_1
    model: gpt-4
    next:
      state_id: end
"""

    try:
        validate_workflow_execution_config_yaml(yaml_text)
        pytest.fail("Expected WorkflowExecutionConfigSchemaValidationError")
    except WorkflowExecutionConfigSchemaValidationError as e:
        error_dict = e.to_dict()

        assert error_dict["error_type"] == "schema_validation"
        assert "errors" in error_dict
        assert len(error_dict["errors"]) > 0

        # Check first error has line number
        first_error = error_dict["errors"][0]
        assert "id" in first_error
        assert "message" in first_error
        assert "path" in first_error
        assert "config_line" in first_error
        assert first_error["config_line"] == 1  # max_concurrency is on line 1


def test_cross_reference_error_with_line_numbers():
    """Test cross-reference error includes line numbers."""
    yaml_text = """max_concurrency: 5
states:
  - id: state_1
    assistant_id: nonexistent_assistant
    model: gpt-4
    next:
      state_id: end
"""

    try:
        validate_workflow_execution_config_yaml(yaml_text)
        pytest.fail("Expected WorkflowExecutionConfigCrossReferenceValidationError")
    except WorkflowExecutionConfigCrossReferenceValidationError as e:
        error_dict = e.to_dict()

        assert error_dict["error_type"] == "cross_reference_validation"
        assert "errors" in error_dict
        assert len(error_dict["errors"]) > 0

        # Check first error has line number
        first_error = error_dict["errors"][0]
        assert "id" in first_error
        assert first_error["message"] == "Invalid reference"
        assert first_error["state_id"] == "state_1"
        assert "config_line" in first_error
        assert first_error["config_line"] == 4  # assistant_id is on line 4


def test_nested_cross_reference_error_with_line_numbers():
    """Test nested cross-reference error (next.state_id) includes line numbers."""
    yaml_text = """states:
  - id: state_1
    assistant_id: asst_1
    model: gpt-4
    next:
      state_id: nonexistent_state
"""

    try:
        validate_workflow_execution_config_yaml(yaml_text)
        pytest.fail("Expected WorkflowExecutionConfigCrossReferenceValidationError")
    except WorkflowExecutionConfigCrossReferenceValidationError as e:
        error_dict = e.to_dict()

        # Find the error for next.state_id
        state_id_error = None
        for error in error_dict["errors"]:
            if error.get("path") == "next.state_id":
                state_id_error = error
                break

        assert state_id_error is not None
        assert state_id_error["state_id"] == "state_1"
        assert "config_line" in state_id_error
        assert state_id_error["config_line"] == 6  # next.state_id is on line 6


def test_error_structure_format():
    """Test that all error types follow the required structure."""
    yaml_text = """max_concurrency: "invalid"
states:
  - id: assistant_1
    assistant_id: asst_1
    model: gpt-4
    next:
      state_id: end
"""

    try:
        validate_workflow_execution_config_yaml(yaml_text)
        pytest.fail("Expected validation error")
    except WorkflowExecutionConfigSchemaValidationError as e:
        error_dict = e.to_dict()

        # Verify top-level structure
        assert "error_type" in error_dict
        assert "message" in error_dict
        assert "errors" in error_dict
        assert isinstance(error_dict["errors"], list)

        # Verify each error has required fields
        for error in error_dict["errors"]:
            assert "id" in error  # UUID
            assert "message" in error  # Short message
            assert "path" in error  # Field path

            # Optional fields
            if "details" in error:
                assert isinstance(error["details"], str)
            if "state_id" in error:
                assert isinstance(error["state_id"], str)
            if "config_line" in error:
                assert isinstance(error["config_line"], int)
                assert error["config_line"] > 0  # 1-indexed


def test_multiple_errors_different_lines():
    """Test that multiple errors in different states have different line numbers."""
    yaml_text = """states:
  - id: state_1
    assistant_id: nonexistent_1
    model: gpt-4
    next:
      state_id: end
  - id: state_2
    assistant_id: nonexistent_2
    model: gpt-3.5
    next:
      state_id: end
"""

    try:
        validate_workflow_execution_config_yaml(yaml_text)
        pytest.fail("Expected WorkflowExecutionConfigCrossReferenceValidationError")
    except WorkflowExecutionConfigCrossReferenceValidationError as e:
        error_dict = e.to_dict()

        assert len(error_dict["errors"]) >= 2

        # Verify each error has a unique line number
        line_numbers = [e["config_line"] for e in error_dict["errors"] if "config_line" in e]
        assert len(line_numbers) >= 2
        assert line_numbers[0] != line_numbers[1]  # Different line numbers


def test_nested_top_level_field_path_display():
    """Test that nested top-level fields show full path (e.g., retry_policy.max_attempts)."""
    yaml_text = """retry_policy:
  max_interval: 9999999
  max_attempts: 999
assistants:
  - id: assistant_1
    assistant_id: uuid
states:
  - id: state_1
    assistant_id: assistant_1
    next:
      state_id: end
"""

    try:
        validate_workflow_execution_config_yaml(yaml_text)
        pytest.fail("Expected WorkflowExecutionConfigSchemaValidationError")
    except WorkflowExecutionConfigSchemaValidationError as e:
        error_dict = e.to_dict()

        assert error_dict["error_type"] == "schema_validation"
        assert "errors" in error_dict
        assert len(error_dict["errors"]) >= 2

        # Find errors related to retry_policy
        retry_policy_errors = [err for err in error_dict["errors"] if "retry_policy" in err.get("path", "")]

        # Verify we have retry_policy errors
        assert len(retry_policy_errors) >= 2

        # Verify paths show full nested structure
        error_paths = {err["path"] for err in retry_policy_errors}
        assert "retry_policy.max_interval" in error_paths, f"Expected 'retry_policy.max_interval' in {error_paths}"
        assert "retry_policy.max_attempts" in error_paths, f"Expected 'retry_policy.max_attempts' in {error_paths}"

        # Verify that errors have correct line numbers
        for err in retry_policy_errors:
            assert "config_line" in err
            assert err["config_line"] >= 2  # retry_policy fields start at line 2
            assert err["config_line"] <= 3  # and end at line 3


def test_cross_reference_error_next_condition_full_path():
    """Cross-reference errors inside next.condition must use full path (next.condition.then/otherwise)."""
    yaml_text = """assistants:
  - id: asst_1
    assistant_id: uuid
states:
  - id: state_1
    assistant_id: asst_1
    next:
      condition:
        expression: "x == 1"
        then: nonexistent_then
        otherwise: nonexistent_otherwise
"""

    try:
        validate_workflow_execution_config_yaml(yaml_text)
        pytest.fail("Expected WorkflowExecutionConfigCrossReferenceValidationError")
    except WorkflowExecutionConfigCrossReferenceValidationError as e:
        error_dict = e.to_dict()
        paths = {err["path"] for err in error_dict["errors"]}
        assert "next.condition.then" in paths
        assert "next.condition.otherwise" in paths
        assert "then" not in paths
        assert "otherwise" not in paths


def test_cross_reference_error_next_switch_full_path():
    """Cross-reference errors inside next.switch must use full path (next.switch.cases[N].state_id / next.switch.default)."""
    yaml_text = """assistants:
  - id: asst_1
    assistant_id: uuid
states:
  - id: state_1
    assistant_id: asst_1
    next:
      switch:
        cases:
          - condition: "x == 1"
            state_id: nonexistent_case
        default: nonexistent_default
"""

    try:
        validate_workflow_execution_config_yaml(yaml_text)
        pytest.fail("Expected WorkflowExecutionConfigCrossReferenceValidationError")
    except WorkflowExecutionConfigCrossReferenceValidationError as e:
        error_dict = e.to_dict()
        paths = {err["path"] for err in error_dict["errors"]}
        assert "next.switch.cases[0].state_id" in paths
        assert "next.switch.default" in paths
        assert "state_id" not in paths
        assert "default" not in paths


def test_cross_reference_error_iter_key_empty_string():
    """Empty iter_key must fail with path 'next.iter_key' and a 'required' message."""
    yaml_text = """assistants:
  - id: asst_1
    assistant_id: uuid
states:
  - id: state_1
    assistant_id: asst_1
    next:
      state_id: state_1
      iter_key: ""
"""

    try:
        validate_workflow_execution_config_yaml(yaml_text)
        pytest.fail("Expected WorkflowExecutionConfigCrossReferenceValidationError")
    except WorkflowExecutionConfigCrossReferenceValidationError as e:
        error_dict = e.to_dict()
        iter_key_errors = [err for err in error_dict["errors"] if err.get("path") == "next.iter_key"]
        assert len(iter_key_errors) == 1
        assert iter_key_errors[0]["message"] == "Iter_key is required"
        assert iter_key_errors[0]["state_id"] == "state_1"


def test_schema_error_iter_key_missing_state_id():
    """iter_key without state_id must fail validation (dependentRequired)."""
    yaml_text = """assistants:
  - id: asst_1
    assistant_id: uuid
states:
  - id: state_1
    assistant_id: asst_1
    next:
      iter_key: items
"""

    with pytest.raises(WorkflowExecutionConfigSchemaValidationError):
        validate_workflow_execution_config_yaml(yaml_text)
