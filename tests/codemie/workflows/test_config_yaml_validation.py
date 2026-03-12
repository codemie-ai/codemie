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

from codemie.workflows.config_yaml_validation import (
    _extract_ids,
    _load_schema_from_file,
    _validate_workflow_execution_config_schema,
    _validate_workflow_execution_config_cross_references,
    validate_workflow_execution_config_yaml,
    CrossRefError,
    WorkflowExecutionParsingError,
    SchemaError,
    WorkflowExecutionConfigSchemaValidationError,
    WorkflowExecutionConfigCrossReferenceValidationError,
    WORKFLOW_EXECUTION_CONFIG_SCHEMA,
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
    assert "errors" in error_dict
    assert len(error_dict["errors"]) > 0

    for error in error_dict["errors"]:
        assert "resource_type" in error
        assert "message" in error
        if "resource_id" in error:
            assert isinstance(error["resource_id"], str)


def test_cross_reference_validation_error_to_dict():
    workflow_config = yaml.safe_load(invalid_cross_ref_yaml)
    errors = _validate_workflow_execution_config_cross_references(workflow_config)

    exception = WorkflowExecutionConfigCrossReferenceValidationError(errors)
    error_dict = exception.to_dict()

    assert error_dict["error_type"] == "cross_reference_validation"
    assert "errors" in error_dict
    assert len(error_dict["errors"]) == 1

    error = error_dict["errors"][0]
    assert error["resource_type"] == "assistant"
    assert error["resource_id"] == "unknown_assistant"
    assert error["reference_state"] == "state_1"
    assert "message" in error
    assert "unknown_assistant" in error["message"]
