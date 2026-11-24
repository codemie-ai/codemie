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

"""Output contract reader (EPMCDME-16503, T3): classification, key recovery, bounds and runtime parity."""

import json
import time
from pathlib import Path

import pytest
import yaml

from codemie.service.assistant_service import AssistantService
from codemie.workflows.contracts.reader import (
    MAX_CONTRACT_BYTES,
    ContractForm,
    ProducerNote,
    classify_output_contract,
)

TEMPLATE = (
    Path(__file__).resolve().parents[4]
    / "config"
    / "templates"
    / "workflow"
    / "test_cases_from_unstructured_data_template.yaml"
)

F = ContractForm
ADVISORY = ProducerNote.CONVERT_TO_SCHEMA
WARNING = ProducerNote.UNKNOWN_SOURCE

SCHEMA_FORM = '{"type":"object","properties":{"a":{"type":"string"}}}'
ARRAY_ITEMS = '{"type":"array","items":{"type":"object","properties":{"a":{"type":"string"},"b":{"type":"integer"}}}}'
ARRAY_PROPS = '{"type":"array","properties":{"a":{"type":"string"}}}'
ARRAY_PROPS_AND_ITEMS = '{"type":"array","properties":{"p":{"type":"string"}},"items":{"type":"object","properties":{"a":{"type":"string"}}}}'
ONBOARDER_TEXT = """[{
  "document": "Document title",
  "type": "Document type"
  "content": "Extracted feature-relevant information"
}]
"""

# (text, form, names, known_source, note, runtime_accepts)
CLASSIFICATION = [
    pytest.param(SCHEMA_FORM, F.SCHEMA, ["a"], True, None, True, id="schema-form"),
    pytest.param('{"type":"object","properties":{}}', F.SCHEMA, [], True, None, True, id="schema-empty-properties"),
    pytest.param(
        '{"type":"string","properties":{"a":{}}}', F.SCHEMA, ["a"], True, None, True, id="schema-non-object-root"
    ),
    pytest.param('{"document":"Document title"}', F.EXAMPLE, ["document"], True, None, False, id="example"),
    pytest.param("{}", F.EXAMPLE, [], False, None, False, id="empty-example-is-not-a-known-source"),
    pytest.param(
        '["List of found features"]', F.LIST, [], False, WARNING, False, id="list-without-dict-items-feature-finder"
    ),
    pytest.param("[]", F.LIST, [], False, WARNING, False, id="empty-list"),
    pytest.param('[{"document":"Document title"}]', F.LIST, ["document"], True, None, False, id="list-of-dicts"),
    pytest.param(
        '[{"a":1},{"b":2},{"a":3},"x"]', F.LIST, ["a", "b"], True, None, False, id="list-union-of-dict-item-keys"
    ),
    pytest.param(
        ONBOARDER_TEXT, F.LIST, ["document", "type", "content"], True, ADVISORY, False, id="onboarder-unparseable"
    ),
    pytest.param(ARRAY_ITEMS, F.LIST, ["a", "b"], True, None, False, id="array-root-items"),
    pytest.param(ARRAY_PROPS, F.LIST, [], False, WARNING, True, id="array-root-properties-runtime-accepts"),
    pytest.param(ARRAY_PROPS_AND_ITEMS, F.LIST, ["a"], True, None, True, id="array-root-properties-ignored-items-win"),
    pytest.param('{"type":"array"}', F.LIST, [], False, WARNING, False, id="array-root-without-items"),
    pytest.param(
        '{"type":["object","null"],"properties":{"a":{"type":"string"}}}',
        F.INVALID,
        [],
        False,
        WARNING,
        False,
        id="type-list-is-not-schema-form",
    ),
    pytest.param('{"type":"object"}', F.INVALID, [], False, WARNING, False, id="type-object-without-properties"),
    pytest.param(
        '{"type":"object","properties":{"a":{"type":"bogus"}}}',
        F.INVALID,
        [],
        False,
        WARNING,
        False,
        id="draft7-invalid-schema",
    ),
    pytest.param('{"a": "x", "b": ...}', F.INVALID, ["a", "b"], True, ADVISORY, False, id="ellipsis-recovered"),
    pytest.param(
        '{\n  // comment\n  "a": "x", /* c */ "b": "y",\n}',
        F.INVALID,
        ["a", "b"],
        True,
        ADVISORY,
        False,
        id="comments-trailing-comma",
    ),
    pytest.param("Return a JSON with the answer", F.INVALID, [], False, WARNING, False, id="prose"),
    pytest.param("42", F.INVALID, [], False, WARNING, False, id="scalar"),
    pytest.param("null", F.INVALID, [], False, WARNING, False, id="json-null"),
    pytest.param("", F.NONE, [], False, None, False, id="empty-text"),
    pytest.param("   \n", F.NONE, [], False, None, False, id="blank-text"),
]


@pytest.mark.parametrize(("text", "form", "names", "known", "note", "_runtime"), CLASSIFICATION)
def test_classification(text, form, names, known, note, _runtime):
    contract = classify_output_contract(text)
    assert contract.form is form
    assert list(contract.names) == names
    assert contract.known_source is known
    assert contract.producer_note is note


PARITY_CASES = [case for case in CLASSIFICATION if case.values[0].strip()]


@pytest.mark.parametrize(("text", "form", "names", "known", "note", "runtime_accepts"), PARITY_CASES)
def test_parity_with_runtime_load_and_validate_schema(text, form, names, known, note, runtime_accepts):
    accepted_by_runtime = AssistantService.load_and_validate_schema(text) is not None
    assert accepted_by_runtime is runtime_accepts
    if form is F.SCHEMA:
        assert accepted_by_runtime


def test_array_root_with_properties_is_a_list_contract_although_runtime_accepts_it():
    assert AssistantService.load_and_validate_schema(ARRAY_PROPS) is not None
    contract = classify_output_contract(ARRAY_PROPS)
    assert contract.form is F.LIST
    assert contract.names == ()
    assert contract.producer_note is WARNING


def test_non_text_values_are_invalid_unknown_sources():
    for value in (5, True, 1.5):
        contract = classify_output_contract(value)
        assert contract.form is F.INVALID
        assert not contract.known_source
        assert contract.producer_note is WARNING
    assert classify_output_contract(None).form is F.NONE


def test_schema_form_fields_carry_types_and_required():
    text = json.dumps(
        {
            "type": "object",
            "properties": {
                "a": {"type": "string"},
                "b": {"type": ["integer", "null"]},
                "c": {"description": "no type"},
                "d": {"type": "null"},
            },
            "required": ["a", "x"],
        }
    )
    fields = {f.name: f for f in classify_output_contract(text).fields}
    assert fields["a"].types == ("string",) and fields["a"].required is True
    assert fields["b"].types == ("integer", "null") and fields["b"].required is False
    assert fields["c"].types is None and fields["c"].required is False
    assert fields["d"].types is None
    assert "x" not in fields


def test_array_root_items_fields_carry_types_from_items_properties():
    contract = classify_output_contract(ARRAY_ITEMS)
    assert {f.name: f.types for f in contract.fields} == {"a": ("string",), "b": ("integer",)}


def test_example_and_recovered_fields_have_unknown_type_and_optionality():
    for text in ('{"a": "x"}', '[{"a": 1}]', '{"a": "x", "b": ...}', ONBOARDER_TEXT):
        for field in classify_output_contract(text).fields:
            assert field.types is None
            assert field.required is None


def test_onboarder_fixture_from_the_real_template():
    data = yaml.safe_load(TEMPLATE.read_text())
    states = data.get("execution_config", data)["states"]
    onboarder = next(s for s in states if s["id"] == "onboarder")
    contract = classify_output_contract(onboarder["output_schema"])
    assert contract.form is F.LIST
    assert list(contract.names) == ["document", "type", "content"]
    assert contract.producer_note is ADVISORY
    assert contract.recovered is True


def test_feature_finder_fixture_is_an_unknown_source_with_a_warning():
    contract = classify_output_contract('["List of found features"]')
    assert contract.form is F.LIST
    assert not contract.known_source
    assert contract.producer_note is WARNING
    assert contract.names == ()


def test_recovery_ignores_keys_inside_strings_nested_objects_and_non_key_strings():
    text = r'{"a": {"inner": 1}, "b": "has \"quoted\": text", "c": [{"deep": 2}], "d" "e": 1 '
    contract = classify_output_contract(text)
    assert contract.form is F.INVALID
    assert list(contract.names) == ["a", "b", "c", "e"]


def test_recovery_of_list_looks_only_one_level_down():
    text = '[{"a": {"nested": 1}, "b": 2, ' + ' "c": [{"deeper": 1}]'
    contract = classify_output_contract(text)
    assert list(contract.names) == ["a", "b", "c"]
    assert contract.recovered is True


def test_recovery_deduplicates_keys_keeping_first_position():
    assert list(classify_output_contract('{"a": 1, "b": 2, "a": 3, ,').names) == ["a", "b"]


def test_recovery_ignores_quoted_text_before_the_object_and_unterminated_strings():
    contract = classify_output_contract('Reply as "x": {"a": 1, "b": "unterminated')
    assert list(contract.names) == ["a", "b"]


@pytest.mark.parametrize("builder", ["bytes", "depth"])
def test_oversize_and_deep_inputs_are_unknown_sources_without_parsing(builder):
    text = '{"a":"' + "x" * MAX_CONTRACT_BYTES + '"}' if builder == "bytes" else "[" * 100_000 + "]" * 100_000
    started = time.monotonic()
    contract = classify_output_contract(text)
    assert time.monotonic() - started < 5
    assert contract.form is F.INVALID
    assert not contract.known_source
    assert contract.names == ()
    assert contract.producer_note is WARNING


def test_text_that_cannot_be_encoded_is_an_invalid_unknown_source_not_a_repaired_copy():
    contract = classify_output_contract('{"a": "' + chr(0xD800) + '"}')

    assert contract.form is ContractForm.INVALID
    assert contract.known_source is False
    assert contract.reason == "not_encodable"


def test_one_megabyte_unparseable_list_is_bounded():
    text = "[{" + ('"k": 1, ' * 140_000)
    assert len(text) > 1_000_000
    contract = classify_output_contract(text)
    assert contract.form is F.INVALID
    assert not contract.known_source


def test_deep_nesting_inside_a_schema_is_rejected_by_depth_bound():
    text = '{"type":"object","properties":{"a":' + '{"items":' * 40 + "{}" + "}" * 40 + "}}"
    assert classify_output_contract(text).form is F.INVALID


@pytest.mark.parametrize("depth", [32])
def test_depth_at_the_limit_is_accepted(depth):
    text = '{"a":' + "[" * (depth - 1) + "]" * (depth - 1) + "}"
    assert classify_output_contract(text).form is F.EXAMPLE


def test_parsed_objects_get_the_same_bounds():
    ok = {"type": "object", "properties": {"a": {"type": "string"}}}
    assert classify_output_contract(ok).form is F.SCHEMA
    deep: dict = {}
    cursor = deep
    for _ in range(40):
        cursor["x"] = {}
        cursor = cursor["x"]
    assert classify_output_contract(deep).form is F.INVALID
    wide = {"type": "object", "properties": {f"k{i}": {"type": "string"} for i in range(MAX_CONTRACT_BYTES)}}
    assert classify_output_contract(wide).form is F.INVALID


def test_cyclic_parsed_object_is_bounded():
    cyclic: dict = {"type": "object"}
    cyclic["properties"] = cyclic
    assert classify_output_contract(cyclic).form is F.INVALID


def test_parsed_list_of_dicts_is_a_list_contract():
    contract = classify_output_contract([{"a": 1}, {"b": 2}])
    assert contract.form is F.LIST
    assert list(contract.names) == ["a", "b"]
