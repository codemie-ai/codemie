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

"""Input contract structural check (EPMCDME-16503, T2, AC-16): check_input_contract and its schema-stage wrapper."""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

from codemie.core.workflow_models import WorkflowConfig
from codemie.workflows.constants import WorkflowErrorType
from codemie.workflows.contracts.input_contract import MAX_INPUT_CONTRACT_BYTES, check_input_contract
from codemie.workflows.validation.schema import (
    WorkflowExecutionConfigSchemaValidationError,
    validate_workflow_execution_config_yaml,
)
from codemie.workflows.workflow import WorkflowExecutor

VALID_CONTRACTS = [
    '{"type":"object","properties":{"doc":{"type":"string"}},"required":["doc"]}',
    '{"type":"object","properties":{}}',
    '{"type":"object","properties":{"a":{"type":"integer"},"b":{"type":["string","null"]}}}',
    '{"type":"object","properties":{"a":{"type":"array","items":{"type":"string"},"description":"x"}},"title":"T"}',
    '{"type":"object","properties":{"a":{"type":"object","properties":{"b":{"$ref":"#/nope"}}}}}',
    '{"type":"object","properties":{"a":{"type":"number"},"b":{"type":"boolean"}},"required":[]}',
    '{"type":"object","properties":{"a":{"type":"string","$ref":"http://example.invalid/x"}}}',
]

INVALID_CONTRACTS = [
    pytest.param('{"doc":"Title"}', id="example-form"),
    pytest.param('{"type":"object"}', id="no-properties"),
    pytest.param('{"type":"string","properties":{}}', id="root-not-object"),
    pytest.param('{"type":["object","null"],"properties":{}}', id="root-type-list"),
    pytest.param('{"type":"object","properties":[]}', id="properties-not-object"),
    pytest.param('{"type":"object","properties":{"a":{}}}', id="entry-without-type"),
    pytest.param('{"type":"object","properties":{"a":{"type":"text"}}}', id="entry-unknown-type"),
    pytest.param('{"type":"object","properties":{"a":{"type":["string","text"]}}}', id="entry-list-unknown-type"),
    pytest.param('{"type":"object","properties":{"a":{"type":[]}}}', id="entry-empty-type-list"),
    pytest.param('{"type":"object","properties":{"a":{"type":5}}}', id="entry-non-string-type"),
    pytest.param('{"type":"object","properties":{"a":true}}', id="entry-not-object"),
    pytest.param('{"type":"object","properties":{"a":{"type":["null"]}}}', id="entry-only-null-list"),
    pytest.param('{"type":"object","properties":{"a":{"type":"null"}}}', id="entry-bare-null"),
    pytest.param('{"type":"object","properties":{"a":{"type":"string"}},"required":["b"]}', id="required-unknown-key"),
    pytest.param('{"type":"object","properties":{"a":{"type":"string"}},"required":"a"}', id="required-not-list"),
    pytest.param('{"type":"object","properties":{"a":{"type":"string"}},"required":[1]}', id="required-not-strings"),
    pytest.param('[{"type":"object","properties":{}}]', id="array-root"),
    pytest.param('[]', id="empty-array"),
    pytest.param('"text"', id="scalar-string"),
    pytest.param('42', id="scalar-number"),
    pytest.param('null', id="scalar-null"),
    pytest.param('true', id="scalar-bool"),
    pytest.param('', id="empty-text"),
    pytest.param('{"type":"object",', id="unparseable"),
    pytest.param('not json at all', id="plain-text"),
    pytest.param(None, id="none"),
    pytest.param(5, id="int"),
    pytest.param({"type": "object", "properties": {}}, id="dict-not-string"),
    pytest.param(["a"], id="list-not-string"),
]


@pytest.mark.parametrize("text", VALID_CONTRACTS)
def test_check_input_contract_accepts_flat_schema_form(text):
    assert check_input_contract(text) == []


@pytest.mark.parametrize("text", INVALID_CONTRACTS)
def test_check_input_contract_rejects_other_shapes(text):
    problems = check_input_contract(text)
    assert problems
    assert all(isinstance(p.message, str) and p.message for p in problems)


def test_check_input_contract_rejects_over_long_digit_literal():
    text = '{"type":"object","properties":{},"x":' + "9" * 5000 + "}"
    assert len(text) < MAX_INPUT_CONTRACT_BYTES
    problems = check_input_contract(text)
    assert [p.code for p in problems] == ["unparseable"]


@pytest.mark.parametrize("depth", [33, 100, 30_000])
def test_check_input_contract_rejects_deeply_nested_arrays_before_parsing(depth):
    text = "[" * depth + "]" * depth
    problems = check_input_contract(text)
    assert [p.code for p in problems] == ["too_deep"]


def test_check_input_contract_depth_guard_cannot_be_driven_negative_by_leading_closers():
    # Closing brackets before the deep run must not buy credit against it.
    text = "]" * 100 + "[" * 40 + "]" * 40
    assert [p.code for p in check_input_contract(text)] == ["too_deep"]


def test_check_input_contract_rejects_deeply_nested_objects_inside_schema():
    inner = '{"a":' * 40 + "1" + "}" * 40
    problems = check_input_contract('{"type":"object","properties":{},"x":' + inner + "}")
    assert [p.code for p in problems] == ["too_deep"]


def test_check_input_contract_allows_depth_32_and_ignores_brackets_in_strings():
    nested = '{"type":"object","properties":{},"x":' + "[" * 28 + "]" * 28 + "}"  # depth 29
    assert check_input_contract(nested) == []
    in_string = '{"type":"object","properties":{},"title":"' + "[" * 100 + '\\"' + "{" * 100 + '"}'
    assert check_input_contract(in_string) == []


def test_check_input_contract_rejects_text_over_64kb_without_parsing():
    padding = "a" * (MAX_INPUT_CONTRACT_BYTES + 1)
    problems = check_input_contract('{"type":"object","properties":{},"title":"' + padding + '"}')
    assert [p.code for p in problems] == ["too_large"]


def test_check_input_contract_counts_bytes_not_characters():
    padding = "я" * (MAX_INPUT_CONTRACT_BYTES // 2)  # two bytes each, pushes the text over the limit
    problems = check_input_contract('{"type":"object","properties":{},"title":"' + padding + '"}')
    assert [p.code for p in problems] == ["too_large"]


def test_check_input_contract_rejects_text_it_cannot_encode_instead_of_measuring_a_repaired_copy():
    lone_surrogate = chr(0xD800)

    problems = check_input_contract('{"type":"object","properties":{},"title":"' + lone_surrogate + '"}')

    assert [p.code for p in problems] == ["not_encodable"]
    assert lone_surrogate not in problems[0].message


def test_check_input_contract_converts_recursion_error_to_problem(monkeypatch):
    def boom(_text):
        raise RecursionError

    monkeypatch.setattr("codemie.workflows.contracts.input_contract.json.loads", boom)
    assert [p.code for p in check_input_contract('{"type":"object","properties":{}}')] == ["unparseable"]


def test_check_input_contract_reports_every_bad_property():
    text = '{"type":"object","properties":{"a":{},"b":{"type":"text"}},"required":["z"]}'
    assert len(check_input_contract(text)) == 3


def test_check_input_contract_messages_do_not_echo_values_or_markup():
    text = json.dumps({"type": "object", "properties": {"<img src=x onerror=1>": {"type": "SECRET-VALUE"}}})
    rendered = " ".join(p.message for p in check_input_contract(text))
    assert "SECRET-VALUE" not in rendered
    assert "<" not in rendered and ">" not in rendered and "&" not in rendered


def test_contracts_module_has_no_core_import():
    import codemie.workflows.contracts.input_contract as module

    source = Path(module.__file__).read_text()
    assert "codemie.core" not in source
    assert "from codemie" not in source and "import codemie" not in source


# --- schema stage wrapper -------------------------------------------------------------------------------------------


def _config_dict() -> dict:
    return {
        "assistants": [{"id": "a1", "system_prompt": "do it"}],
        "states": [
            {"id": "s1", "assistant_id": "a1", "next": {"state_id": "end"}},
            {"id": "orphan", "assistant_id": "a1", "next": {"state_id": "end"}},
        ],
    }


def _errors_for(config_dict: dict) -> list[dict]:
    with pytest.raises(WorkflowExecutionConfigSchemaValidationError) as exc_info:
        validate_workflow_execution_config_yaml(yaml.safe_dump(config_dict))
    assert exc_info.value.to_dict()["error_type"] == WorkflowErrorType.SCHEMA_VALIDATION.value
    return exc_info.value.to_dict()["errors"]


@pytest.mark.parametrize("text", [p.values[0] for p in INVALID_CONTRACTS if isinstance(p.values[0], str)])
def test_schema_stage_rejects_invalid_input_schema_with_state_id_and_path(text):
    config_dict = _config_dict()
    config_dict["states"][0]["input_schema"] = text
    errors = _errors_for(config_dict)
    assert [(e["state_id"], e["path"]) for e in errors] == [("s1", "input_schema")] * len(errors)
    assert errors[0]["message"]


def test_schema_stage_checks_unreachable_state():
    config_dict = _config_dict()
    config_dict["states"][1]["input_schema"] = '{"doc":"Title"}'
    errors = _errors_for(config_dict)
    assert {e["state_id"] for e in errors} == {"orphan"}


def test_input_contract_stage_checks_orphaned_states_like_states():
    from codemie.workflows.contracts.input_contract_stage import validate_state_input_contracts

    workflow_config = {
        "states": [{"id": "s1", "input_schema": '{"type":"object","properties":{}}'}],
        "orphaned_states": [{"id": "lost", "input_schema": '{"doc":"Title"}'}, {"id": "ok"}],
    }

    errors = validate_state_input_contracts(workflow_config)

    assert errors
    assert {tuple(error.path) for error in errors} == {("orphaned_states", 0, "input_schema")}


def test_schema_stage_reports_non_string_input_schema_once():
    config_dict = _config_dict()
    config_dict["states"][0]["input_schema"] = {"type": "object", "properties": {}}
    errors = _errors_for(config_dict)
    assert len(errors) == 1 and errors[0]["state_id"] == "s1" and errors[0]["path"] == "input_schema"


def test_schema_stage_reports_every_bad_state_together():
    config_dict = _config_dict()
    config_dict["states"][0]["input_schema"] = "[]"
    config_dict["states"][1]["input_schema"] = "{"
    assert {e["state_id"] for e in _errors_for(config_dict)} == {"s1", "orphan"}


@pytest.mark.parametrize("text", VALID_CONTRACTS)
def test_schema_stage_accepts_valid_input_schema(text):
    config_dict = _config_dict()
    config_dict["states"][0]["input_schema"] = text
    validate_workflow_execution_config_yaml(yaml.safe_dump(config_dict))


def test_schema_stage_without_input_schema_is_unchanged():
    validate_workflow_execution_config_yaml(yaml.safe_dump(_config_dict()))


def test_schema_stage_string_format_carries_the_problem_text():
    config_dict = _config_dict()
    config_dict["states"][0]["input_schema"] = '{"type":"object"}'
    with pytest.raises(WorkflowExecutionConfigSchemaValidationError) as exc_info:
        validate_workflow_execution_config_yaml(yaml.safe_dump(config_dict))
    assert "input_schema" in str(exc_info.value)
    assert "properties" in str(exc_info.value)


@pytest.mark.parametrize("error_format", ["json", "string"])
@pytest.mark.parametrize("link_contracts", [False])
def test_validate_workflow_rejects_bad_input_schema_in_every_mode(error_format, link_contracts):
    config_dict = _config_dict()
    config_dict["states"][1]["input_schema"] = '{"doc":"Title"}'
    config_dict["save_validation"] = "warn"
    stored = WorkflowConfig(
        id="wf-1",
        name="wf",
        description="d",
        project="demo",
        mode="Sequential",
        yaml_config=yaml.safe_dump(config_dict),
    )

    with pytest.raises(ValueError) as exc_info:
        WorkflowExecutor.validate_workflow(stored, MagicMock(id="u1"), error_format=error_format)

    if error_format == "json":
        error = exc_info.value.args[0]
        assert error["error_type"] == WorkflowErrorType.SCHEMA_VALIDATION.value
        assert any(e.get("state_id") == "orphan" and e["path"] == "input_schema" for e in error["errors"])
    else:
        assert "input_schema" in str(exc_info.value)
