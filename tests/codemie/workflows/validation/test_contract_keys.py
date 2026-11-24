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

"""Contract keys (EPMCDME-16503, T1): mode enum, error type, lenient state fields and YAML-schema keys."""

import copy
import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from codemie.core.workflow_models import WorkflowConfig, WorkflowState
from codemie.workflows.constants import ContractValidationMode, WorkflowErrorType
from codemie.workflows.validation.schema import (
    WORKFLOW_EXECUTION_CONFIG_SCHEMA,
    WorkflowExecutionConfigSchemaValidationError,
    validate_workflow_execution_config_yaml,
)
from codemie.workflows.workflow import WorkflowExecutor

TEMPLATES_DIR = Path(__file__).resolve().parents[4] / "config" / "templates" / "workflow"


def _base_config() -> dict:
    return {
        "assistants": [{"id": "a1", "system_prompt": "do it"}],
        "states": [
            {"id": "s1", "assistant_id": "a1", "next": {"state_id": "s2"}},
            {"id": "s2", "assistant_id": "a1", "next": {"state_id": "end"}},
        ],
    }


def _config(config_dict: dict) -> WorkflowConfig:
    return WorkflowConfig(
        id="wf-1",
        name="wf",
        description="d",
        project="demo",
        mode="Sequential",
        yaml_config=yaml.safe_dump(config_dict),
    )


LENIENT_STATE_KEYS = [
    ("save_validation", "foo"),
    ("save_validation", "off"),
    ("save_validation", 1),
    ("save_validation", {"a": 1}),
    ("input_schema", {"a": 1}),
    ("input_schema", '{"type": "object", "properties": {}}'),
]


def test_error_type_member_value():
    assert WorkflowErrorType.CONTRACT_VALIDATION.value == "contract_validation"


def test_mode_enum_values():
    assert {m.value for m in ContractValidationMode} == {"strict", "warn"}


def test_mode_enum_equals_state_and_workflow_yaml_enums():
    mode_values = {m.value for m in ContractValidationMode}
    state_def = WORKFLOW_EXECUTION_CONFIG_SCHEMA["$defs"]["State"]["properties"]
    assert set(state_def["save_validation"]["enum"]) == mode_values
    assert set(WORKFLOW_EXECUTION_CONFIG_SCHEMA["properties"]["save_validation"]["enum"]) == mode_values


def test_yaml_schema_declares_input_schema_string_and_require_flag_boolean():
    state_def = WORKFLOW_EXECUTION_CONFIG_SCHEMA["$defs"]["State"]["properties"]
    assert state_def["input_schema"] == {"type": "string"}
    assert WORKFLOW_EXECUTION_CONFIG_SCHEMA["properties"]["require_input_contracts"] == {"type": "boolean"}


def _loaded(key, value):
    """What the model keeps: `save_validation` keeps any value, `input_schema` keeps text and drops everything else."""
    return None if key == "input_schema" and not isinstance(value, str) else value


@pytest.mark.parametrize("key, value", LENIENT_STATE_KEYS)
def test_workflow_state_accepts_any_value_for_new_keys(key, value):
    state = WorkflowState(id="s1", assistant_id="a1", next={"state_id": "end"}, **{key: value})
    assert getattr(state, key) == _loaded(key, value)


def test_workflow_state_new_keys_default_to_none():
    state = WorkflowState(id="s1", assistant_id="a1", next={"state_id": "end"})
    assert state.input_schema is None
    assert state.save_validation is None


@pytest.mark.parametrize("key, value", LENIENT_STATE_KEYS)
def test_stored_workflow_with_bad_new_keys_loads_via_get_workflow(key, value):
    config_dict = _base_config()
    config_dict["states"][0][key] = value
    stored = _config(config_dict)

    with patch.object(WorkflowConfig, "get_by_id", return_value=stored):
        from codemie.service.workflow_service import WorkflowService

        loaded = WorkflowService().get_workflow("wf-1")

    assert getattr(loaded.states[0], key) == _loaded(key, value)
    assert getattr(loaded.states[1], key) is None


@pytest.mark.parametrize("key, value", LENIENT_STATE_KEYS)
def test_stored_workflow_with_bad_new_keys_loads_via_create_executor(key, value):
    config_dict = _base_config()
    config_dict["states"][0][key] = value
    stored = _config(config_dict)

    with patch("codemie.workflows.workflow.WorkflowExecutionService"):
        executor = WorkflowExecutor.create_executor(stored, "input", MagicMock(id="u1"))

    assert getattr(executor.workflow_config.states[0], key) == _loaded(key, value)


@pytest.mark.parametrize(
    "key, value",
    [
        ("save_validation", "foo"),
        ("save_validation", "off"),
        ("save_validation", 1),
        ("save_validation", {"a": 1}),
        ("input_schema", {"a": 1}),
    ],
)
def test_validate_workflow_rejects_bad_state_keys_at_schema_stage_with_state_id_and_path(key, value):
    config_dict = _base_config()
    config_dict["states"][1][key] = value
    stored = _config(config_dict)

    with pytest.raises(ValueError) as exc_info:
        WorkflowExecutor.validate_workflow(stored, MagicMock(id="u1"), error_format="json")

    error = exc_info.value.args[0]
    assert error["error_type"] == WorkflowErrorType.SCHEMA_VALIDATION.value
    assert any(e.get("state_id") == "s2" and key in e["path"] for e in error["errors"]), error


@pytest.mark.parametrize("value", ["strict", "warn"])
def test_schema_accepts_valid_state_validation_and_string_input_schema(value):
    state_yaml = _base_config()
    state_yaml["states"][0]["save_validation"] = value
    state_yaml["states"][0]["input_schema"] = '{"type": "object", "properties": {"a": {"type": "string"}}}'
    validate_workflow_execution_config_yaml(yaml.safe_dump(state_yaml))


@pytest.mark.parametrize("value", ["strict", "warn"])
def test_schema_accepts_valid_workflow_level_keys(value):
    state_yaml = _base_config()
    state_yaml["save_validation"] = value
    state_yaml["require_input_contracts"] = True
    validate_workflow_execution_config_yaml(yaml.safe_dump(state_yaml))


@pytest.mark.parametrize(
    "key, value, expected_path",
    [
        ("save_validation", "off", "save_validation"),
        ("save_validation", 1, "save_validation"),
        ("require_input_contracts", "yes", "require_input_contracts"),
        ("require_input_contracts", 1, "require_input_contracts"),
    ],
)
def test_schema_rejects_bad_workflow_level_keys(key, value, expected_path):
    state_yaml = _base_config()
    state_yaml[key] = value
    with pytest.raises(WorkflowExecutionConfigSchemaValidationError) as exc_info:
        validate_workflow_execution_config_yaml(yaml.safe_dump(state_yaml))
    paths = [e["path"] for e in exc_info.value.to_dict()["errors"]]
    assert expected_path in paths


def _template_files() -> list[Path]:
    return sorted(TEMPLATES_DIR.glob("*.yaml"))


@pytest.mark.parametrize("template", _template_files(), ids=lambda p: p.name)
def test_builtin_templates_are_not_newly_rejected(template):
    """Templates carry none of the new keys, so the new schema entries must not change their outcome."""
    # Templates carry unresolved ${input:...} placeholders that are not valid YAML flow content.
    data = yaml.safe_load(re.sub(r"\$\{[^}]*\}", "placeholder", template.read_text()))
    execution_config = data.get("execution_config") or {}
    if not execution_config:
        pytest.skip("template has no execution_config")
    execution_yaml = yaml.safe_dump(execution_config)

    assert _outcome(execution_yaml) == _outcome(execution_yaml, schema=_schema_without_contract_keys())


def _schema_without_contract_keys() -> dict:
    schema = copy.deepcopy(WORKFLOW_EXECUTION_CONFIG_SCHEMA)
    schema["properties"].pop("save_validation", None)
    schema["properties"].pop("require_input_contracts", None)
    schema["$defs"]["State"]["properties"].pop("save_validation", None)
    schema["$defs"]["State"]["properties"].pop("input_schema", None)
    return schema


def _outcome(execution_yaml: str, schema: dict | None = None) -> list[str]:
    from codemie.workflows.validation.schema import _validate_workflow_execution_config_schema

    errors = _validate_workflow_execution_config_schema(
        schema or WORKFLOW_EXECUTION_CONFIG_SCHEMA, yaml.safe_load(execution_yaml)
    )
    return sorted(str(e.message) for e in errors)
