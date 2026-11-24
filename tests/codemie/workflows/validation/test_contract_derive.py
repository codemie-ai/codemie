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

"""Requirement derivation (EPMCDME-16503, T4): AC-S1..AC-S4, AC-37 and the AC-13 / AC-15 derivations."""

import inspect
import json
from types import SimpleNamespace

import pytest

from codemie.core.workflow_models.workflow_models import (
    CustomWorkflowNode,
    WorkflowState,
    WorkflowTool,
)
from codemie.workflows import workflow as workflow_module
from codemie.workflows.constants import FILE_NAMES_CONTEXT_KEY, PROPAGATED_HEADERS_CONTEXT_KEY
from codemie.workflows.contracts.derive import (
    ALLOWED_FUNCTION_NAMES,
    LABEL_RENDERS_EMPTY,
    ORIGIN_CONDITION,
    ORIGIN_EXTRACT,
    ORIGIN_EXTRACT_INPUT_KEY,
    ORIGIN_INPUT_SCHEMA,
    ORIGIN_ITER_KEY,
    ORIGIN_PLACEHOLDER,
    ORIGIN_SWITCH,
    ORIGIN_TOOL_BINDING,
    ORIGIN_TOOL_BINDING_INPUT_KEY,
    ORIGIN_TOOL_BINDING_OVERRIDE,
    ORIGIN_TOOL_BINDING_TEMPLATE,
    PLATFORM_CONTEXT_KEYS,
    REASON_ITER_KEY_NOT_ARRAY,
    REASON_LIST_OUTPUT,
    REASON_NOT_IN_OUTPUT_CONTRACT,
    REASON_NO_USABLE_OUTPUT_CONTRACT,
    REASON_OK,
    RENDERS_EMPTY_ORIGINS,
    UNVERIFIABLE_ORIGINS,
    derive_state_requirements,
    extract_expression_names,
)
from codemie.workflows.contracts.jinja_analysis import extract_context_names
from codemie.workflows.contracts.models import Outcome, RecordKind
from codemie.workflows.utils.safe_eval import ALLOWED_FUNCTIONS
from codemie.workflows.utils.json_utils import UnwrappingJsonPointerEvaluator
from codemie.workflows.utils.transform_node_utils import extract_field

SCHEMA_CONTRACT = '{"type":"object","properties":{"doc":{"type":"string"},"items":{"type":"array"}},"required":["doc"]}'
EXAMPLE_CONTRACT = '{"success": "Boolean true | false", "issues": "List of issues"}'
RECOVERED_CONTRACT = '{\n  "all_done": "Boolean true | false"\n  "note": "unparseable, no comma above"\n}'
LIST_CONTRACT = '[{"document": "Document title", "content": "Extracted information"}]'
UNKNOWN_LIST_CONTRACT = '["List of found features"]'

# Copied from config/templates/workflow/workflow_branch_comparison_template.yaml:45-69 (a corpus-style template body:
# {% set %} / {% for %} / {%- if %}, filters and a pasted JSON block).
CORPUS_TEMPLATE_TASK = """Summarise the findings for {{ project_name }}.
Input payload pasted by the author:
{"pull_request": {"title": "x"}, "number": 7}
{% set grouped_items = {} -%}
{% for item in items -%}
    {%- if item and item.conclusion != "" and "file_path" in item %}
        {%- set directory = item.file_path.rsplit('/', 1)[0] -%}
    {%- endif %}
{%- endfor -%}
{%- set sorted_directories = grouped_items.keys() | sort -%}
{% for directory in sorted_directories %}
### {{ directory }}
  {{ conclusion | safe }}
{%- endfor %}
Report owner: {{ owner.name }}
"""


def _state(**overrides) -> WorkflowState:
    data = {"id": "consumer", "assistant_id": "assistant-1", "next": {"state_id": "next-state"}}
    data.update(overrides)
    return WorkflowState(**data)


def _transform(config: dict) -> CustomWorkflowNode:
    return CustomWorkflowNode(id="transform-1", custom_node_id="transform_node", config=config)


def _summary(requirements) -> list[tuple]:
    return [(item.path, item.name, item.origin, item.required) for item in requirements]


def _names(requirements, origin: str) -> list[str]:
    return [item.name for item in requirements if item.origin == origin]


# --- placeholder extraction (AC-S1, AR-18) ------------------------------------------------------

# (template text, expected root names)
PLACEHOLDERS = [
    pytest.param("{{ name }}", ["name"], id="plain"),
    pytest.param("{{name}}", ["name"], id="plain-no-spaces"),
    pytest.param("{{ owner.name }}", ["owner"], id="dotted-root-only"),
    pytest.param("{{- name -}}", ["name"], id="whitespace-control"),
    pytest.param("a {{ one }} b {{ two }} c", ["one", "two"], id="several"),
    pytest.param("{{ name }} and {{ name.path }}", ["name"], id="deduplicated"),
    pytest.param("{{ name | default('x') }}", [], id="under-default-gives-none"),
    pytest.param("{{ name | upper }}", [], id="filtered-is-not-plain"),
    pytest.param("{{ items[0] }}", [], id="subscript-is-not-plain"),
    pytest.param("{{ len(items) }}", [], id="call-is-not-plain"),
    pytest.param("{% if flag %}{{ body }}{% endif %}", ["body"], id="names-in-block-tags-are-skipped"),
    pytest.param("{% for item in items %}{{ item }}{% endfor %}", [], id="loop-local"),
    pytest.param("{% for a, b in pairs %}{{ a }}{{ b }}{% endfor %}", [], id="tuple-loop-locals"),
    pytest.param("{% for item in items %}{{ other }}{% endfor %}", ["other"], id="non-local-inside-loop"),
    pytest.param("{% set alias = 1 %}{{ alias }}", [], id="set-target-is-local"),
    pytest.param("{% for item in items %}{{ loop.index }}. {{ item }}{% endfor %}", [], id="loop-object-is-built-in"),
    pytest.param("{{ loop.first }}{{ real }}", ["loop", "real"], id="loop-outside-a-loop-is-a-context-read"),
    pytest.param("{{ true }}{{ false }}{{ none }}{{ True }}{{ False }}{{ None }}", [], id="literals-are-built-in"),
    pytest.param("{{ truthy }}{{ looped.index }}", ["truthy", "looped"], id="built-in-lookalikes-stay-fields"),
    pytest.param('{"document": "x", "type": "y"}', [], id="pasted-json-single-braces"),
    pytest.param('{{"document": "x"}}', [], id="pasted-json-double-braces"),
    pytest.param("unterminated {{ name ", [], id="unterminated-expression-renders-nothing"),
    pytest.param("unterminated {% if flag %} {{ name }}", [], id="unterminated-block"),
    pytest.param("{% raw %}{{ name }}{% endraw %}", [], id="raw-block-bails-out"),
    pytest.param("{# {{ name }} #}", [], id="comment"),
    pytest.param("", [], id="empty"),
    pytest.param(None, [], id="not-a-string"),
    pytest.param(CORPUS_TEMPLATE_TASK, ["project_name", "owner"], id="corpus-style-template"),
]


@pytest.mark.parametrize(("text", "expected"), PLACEHOLDERS)
def test_extract_placeholder_names(text, expected):
    assert list(extract_context_names(text)) == expected


# --- AC-S1: agent placeholders ------------------------------------------------------------------


def test_s1_derives_requirements_and_covers_the_state_when_the_flag_is_on():
    state = _state(task="Use {{ report }} and {{ owner.name }}", resolve_dynamic_values_in_prompt=True)

    result = derive_state_requirements(state)

    assert _summary(result.context_requirements) == [
        ("task", "report", ORIGIN_PLACEHOLDER, True),
        ("task", "owner", ORIGIN_PLACEHOLDER, True),
    ]
    assert result.covered is True
    assert result.coverage_source == ORIGIN_PLACEHOLDER
    assert all(item.kind is RecordKind.DERIVED for item in result.context_requirements)


def test_s1_derives_nothing_with_the_flag_off():
    state = _state(task="Use {{ report }}", resolve_dynamic_values_in_prompt=False)

    result = derive_state_requirements(state)

    assert result.context_requirements == ()
    assert result.covered is False


def test_s1_derives_nothing_without_references():
    state = _state(task="Plain task text", resolve_dynamic_values_in_prompt=True)

    result = derive_state_requirements(state)

    assert result.context_requirements == ()
    assert result.covered is False


def test_s1_only_applies_to_agent_states():
    state = _state(
        id="tool-state",
        assistant_id=None,
        tool_id="tool-1",
        task="Use {{ report }}",
        resolve_dynamic_values_in_prompt=True,
    )

    result = derive_state_requirements(state, tool=WorkflowTool(id="tool-1", tool="generic_tool"))

    assert _names(result.context_requirements, ORIGIN_PLACEHOLDER) == []


def test_s1_requirements_carry_the_renders_empty_label_origin():
    assert LABEL_RENDERS_EMPTY == "renders empty"
    assert ORIGIN_PLACEHOLDER in RENDERS_EMPTY_ORIGINS
    assert ORIGIN_TOOL_BINDING_TEMPLATE in RENDERS_EMPTY_ORIGINS
    assert ORIGIN_INPUT_SCHEMA not in RENDERS_EMPTY_ORIGINS


# --- AC-S2: transform extract roots -------------------------------------------------------------

# (transform config, expected (path, name, origin, required) rows, covered)
EXTRACT_CASES = [
    pytest.param(
        {"mappings": [{"output_field": "n", "type": "extract", "source_path": "number"}]},
        [("config.mappings[0].source_path", "number", ORIGIN_EXTRACT, True)],
        True,
        id="extract-root",
    ),
    pytest.param(
        {"mappings": [{"output_field": "u", "source_path": "pull_request.user.login"}]},
        [("config.mappings[0].source_path", "pull_request", ORIGIN_EXTRACT, True)],
        True,
        id="extract-default-type-and-dotted-path",
    ),
    pytest.param(
        {"mappings": [{"output_field": "d", "type": "extract", "source_path": "pull_request.draft", "default": False}]},
        [("config.mappings[0].source_path", "pull_request", ORIGIN_EXTRACT, False)],
        True,
        id="extract-with-default-is-optional",
    ),
    pytest.param(
        {
            "mappings": [
                {"output_field": "a", "type": "extract", "source_path": "payload"},
                {"output_field": "b", "type": "extract", "source_path": "payload"},
            ]
        },
        [("config.mappings[0].source_path", "payload", ORIGIN_EXTRACT, True)],
        True,
        id="same-root-twice-is-one-requirement",
    ),
    pytest.param(
        {
            "mappings": [
                {"output_field": "first", "type": "extract", "source_path": "payload"},
                {"output_field": "second", "type": "extract", "source_path": "first.name"},
            ]
        },
        [("config.mappings[0].source_path", "payload", ORIGIN_EXTRACT, True)],
        True,
        id="root-produced-by-an-earlier-mapping-is-not-a-context-read",
    ),
    pytest.param(
        {
            "input_key": "stats.total",
            "mappings": [{"output_field": "c", "type": "extract", "source_path": "count"}],
        },
        [("config.input_key", "stats", ORIGIN_EXTRACT_INPUT_KEY, True)],
        True,
        id="input-key-is-the-root",
    ),
    pytest.param(
        {
            "input_key": "users[0]",
            "mappings": [{"output_field": "c", "type": "extract", "source_path": "name"}],
        },
        [("config.input_key", "users", ORIGIN_EXTRACT_INPUT_KEY, True)],
        True,
        id="input-key-with-array-index",
    ),
    pytest.param(
        {
            "mappings": [
                {"output_field": "t", "type": "template", "template": "PR #{{ number }}"},
                {"output_field": "c", "type": "condition", "condition": "action == 'opened'"},
                {"output_field": "s", "type": "script", "script": "len(items)"},
                {"output_field": "k", "type": "constant", "value": 1},
                {"output_field": "m", "type": "array_map", "item_field": "name"},
            ]
        },
        [],
        False,
        id="non-extract-mappings-derive-nothing",
    ),
    pytest.param(
        {
            "input_source": "messages",
            "mappings": [{"output_field": "a", "type": "extract", "source_path": "payload"}],
        },
        [],
        False,
        id="input-source-messages",
    ),
    pytest.param(
        {
            "input_source": "user_input",
            "mappings": [{"output_field": "a", "type": "extract", "source_path": "payload"}],
        },
        [],
        False,
        id="input-source-user-input",
    ),
    pytest.param(
        {
            "input_source": "state_schema",
            "mappings": [{"output_field": "a", "type": "extract", "source_path": "payload"}],
        },
        [],
        False,
        id="input-source-state-schema",
    ),
    pytest.param({"mappings": []}, [], False, id="no-mappings-is-uncovered"),
    pytest.param({}, [], False, id="empty-config-is-uncovered"),
]


@pytest.mark.parametrize(("config", "expected", "covered"), EXTRACT_CASES)
def test_s2_transform_extract_requirements(config, expected, covered):
    state = _state(assistant_id=None, custom_node_id="transform-1")

    result = derive_state_requirements(state, custom_node=_transform(config))

    assert _summary(result.context_requirements) == expected
    assert result.covered is covered


def test_extract_path_with_an_index_is_read_as_the_runtime_reads_it():
    config = {"mappings": [{"output_field": "picked", "type": "extract", "source_path": "items[0].x"}]}
    state = _state(assistant_id=None, custom_node_id="transform-1")

    result = derive_state_requirements(state, custom_node=_transform(config))

    assert _summary(result.context_requirements) == [
        ("config.mappings[0].source_path", "items[0]", ORIGIN_EXTRACT, True)
    ]
    assert extract_field({"items": [{"x": "first"}]}, "items[0].x") is None
    assert extract_field({"items[0]": {"x": "first"}}, "items[0].x") == "first"


def test_s2_transition_requirements_do_not_cover_a_transform():
    state = _state(
        assistant_id=None,
        custom_node_id="transform-1",
        output_schema=EXAMPLE_CONTRACT,
        next={"state_id": "x", "iter_key": "issues"},
    )

    result = derive_state_requirements(state, custom_node=_transform({"mappings": []}))

    assert result.transition_requirements
    assert result.covered is False


def test_s2_is_not_applied_to_other_custom_node_kinds():
    state = _state(assistant_id=None, custom_node_id="other-1")
    node = CustomWorkflowNode(
        id="other-1",
        custom_node_id="state_processor_node",
        config={"mappings": [{"output_field": "a", "type": "extract", "source_path": "payload"}]},
    )

    result = derive_state_requirements(state, custom_node=node)

    assert result.context_requirements == ()
    assert result.covered is False


# --- AC-S3 / AC-13 / AC-14 / AC-15: tool bindings -----------------------------------------------


def test_s3_state_bindings_replace_tool_bindings():
    state = _state(
        assistant_id=None,
        tool_id="tool-1",
        tool_args={"query": None},
    )
    tool = WorkflowTool(id="tool-1", tool="generic_tool", tool_args={"ignored": None})

    result = derive_state_requirements(state, tool=tool)

    assert _summary(result.context_requirements) == [("tool_args.query", "query", ORIGIN_TOOL_BINDING, True)]


def test_s3_tool_bindings_are_used_when_the_state_has_none():
    state = _state(assistant_id=None, tool_id="tool-1")
    tool = WorkflowTool(id="tool-1", tool="generic_tool", tool_args={"query": ""})

    result = derive_state_requirements(state, tool=tool)

    assert _summary(result.context_requirements) == [("tool_args.query", "query", ORIGIN_TOOL_BINDING, True)]


def test_ac13_template_binding_derives_the_referenced_root():
    state = _state(assistant_id=None, tool_id="tool-1", tool_args={"path": "{{ project_base }}/src"})
    tool = WorkflowTool(id="tool-1", tool="generic_tool")

    result = derive_state_requirements(state, tool=tool)

    assert ("tool_args.path", "project_base", ORIGIN_TOOL_BINDING_TEMPLATE, True) in _summary(
        result.context_requirements
    )


def test_ac15_applies_to_a_template_binding_too():
    state = _state(assistant_id=None, tool_id="tool-1", tool_args={"path": "{{ project_base }}/src"})

    result = derive_state_requirements(state, tool=WorkflowTool(id="tool-1", tool="generic_tool"))

    assert ("tool_args.path", "path", ORIGIN_TOOL_BINDING_OVERRIDE, False) in _summary(result.context_requirements)


def test_ac15_literal_binding_with_a_same_named_context_field_is_derived_as_optional():
    state = _state(assistant_id=None, tool_id="tool-1", tool_args={"query": "a literal value"})
    tool = WorkflowTool(id="tool-1", tool="generic_tool")

    result = derive_state_requirements(state, tool=tool)

    assert _summary(result.context_requirements) == [("tool_args.query", "query", ORIGIN_TOOL_BINDING_OVERRIDE, False)]


def test_ac13_and_ac15_derive_requirements_without_an_outcome():
    state = _state(assistant_id=None, tool_id="tool-1", tool_args={"query": "literal", "path": "{{ missing }}"})

    result = derive_state_requirements(state, tool=WorkflowTool(id="tool-1", tool="generic_tool"))

    assert _summary(result.context_requirements) == [
        ("tool_args.query", "query", ORIGIN_TOOL_BINDING_OVERRIDE, False),
        ("tool_args.path", "path", ORIGIN_TOOL_BINDING_OVERRIDE, False),
        ("tool_args.path", "missing", ORIGIN_TOOL_BINDING_TEMPLATE, True),
    ]
    assert not hasattr(result.context_requirements[0], "outcome")


def test_s3_input_key_bindings_are_unverifiable():
    state = _state(assistant_id=None, tool_id="tool-1", tool_args={"query": None, "path": "{{ root }}"})
    tool = WorkflowTool(id="tool-1", tool="generic_tool", input_key="payload")

    result = derive_state_requirements(state, tool=tool)

    assert _summary(result.context_requirements) == [
        ("tool_args.query", "query", ORIGIN_TOOL_BINDING_INPUT_KEY, False),
        ("tool_args.path", "path", ORIGIN_TOOL_BINDING_INPUT_KEY, False),
    ]
    assert ORIGIN_TOOL_BINDING_INPUT_KEY in UNVERIFIABLE_ORIGINS


def test_ac14b_empty_bindings_use_the_mandatory_parameters_of_the_args_schema():
    state = _state(assistant_id=None, tool_id="tool-1")
    tool = WorkflowTool(id="tool-1", tool="generic_tool")

    result = derive_state_requirements(state, tool=tool, mandatory_parameters=frozenset({"query"}))

    assert _summary(result.context_requirements) == [("tool_args.query", "query", ORIGIN_TOOL_BINDING, True)]
    assert result.covered is True
    assert result.coverage_source == ORIGIN_TOOL_BINDING
    assert result.tool_schema_unknown is False


def test_ac38_a_tool_without_an_args_schema_is_unverified_and_uncovered():
    state = _state(assistant_id=None, tool_id="tool-1", tool_args={"query": None})

    result = derive_state_requirements(state, tool=WorkflowTool(id="tool-1", tool="generic_tool"))

    assert result.tool_schema_unknown is True
    assert result.covered is False
    assert result.missing_mandatory_parameters == ()


def test_ac14a_non_empty_bindings_that_omit_a_mandatory_parameter_are_reported():
    state = _state(assistant_id=None, tool_id="tool-1", tool_args={"other": None})
    tool = WorkflowTool(id="tool-1", tool="generic_tool")

    result = derive_state_requirements(state, tool=tool, mandatory_parameters=frozenset({"query", "other"}))

    assert result.missing_mandatory_parameters == ("query",)
    assert result.covered is True


# --- AC-S4: platform keys -----------------------------------------------------------------------


def test_s4_platform_keys_match_the_runtime_seeding():
    source = inspect.getsource(workflow_module.WorkflowExecutor.on_workflow_start)

    assert "initial_context[FILE_NAMES_CONTEXT_KEY]" in source
    assert "initial_context[PROPAGATED_HEADERS_CONTEXT_KEY]" in source
    assert frozenset({FILE_NAMES_CONTEXT_KEY, PROPAGATED_HEADERS_CONTEXT_KEY}) == PLATFORM_CONTEXT_KEYS


@pytest.mark.parametrize("key", sorted(PLATFORM_CONTEXT_KEYS))
def test_s4_a_binding_to_a_platform_key_is_never_required(key):
    state = _state(assistant_id=None, tool_id="tool-1", tool_args={key: None})

    result = derive_state_requirements(state, tool=WorkflowTool(id="tool-1", tool="generic_tool"))

    assert _summary(result.context_requirements) == [(f"tool_args.{key}", key, ORIGIN_TOOL_BINDING, False)]


def test_s4_a_placeholder_on_a_platform_key_is_never_required():
    state = _state(task="Attached: {{ file_names }}", resolve_dynamic_values_in_prompt=True)

    result = derive_state_requirements(state)

    assert _summary(result.context_requirements) == [("task", "file_names", ORIGIN_PLACEHOLDER, False)]


# --- declared requirements ----------------------------------------------------------------------


def test_declared_input_contract_requirements_and_coverage():
    state = _state(input_schema=SCHEMA_CONTRACT)

    result = derive_state_requirements(state)

    assert _summary(result.context_requirements) == [
        ("input_schema.doc", "doc", ORIGIN_INPUT_SCHEMA, True),
        ("input_schema.items", "items", ORIGIN_INPUT_SCHEMA, False),
    ]
    assert [item.types for item in result.context_requirements] == [("string",), ("array",)]
    assert [item.kind for item in result.context_requirements] == [RecordKind.DECLARED] * 2
    assert result.covered is True
    assert result.coverage_source == ORIGIN_INPUT_SCHEMA


def test_declared_contract_does_not_switch_derivation_off():
    state = _state(
        input_schema='{"type":"object","properties":{"doc":{"type":"string"}}}',
        task="Use {{ report }}",
        resolve_dynamic_values_in_prompt=True,
    )

    result = derive_state_requirements(state)

    assert _names(result.context_requirements, ORIGIN_INPUT_SCHEMA) == ["doc"]
    assert _names(result.context_requirements, ORIGIN_PLACEHOLDER) == ["report"]


def test_a_non_conforming_declared_contract_derives_nothing_and_does_not_cover():
    state = _state(input_schema='{"doc": "Title"}')

    result = derive_state_requirements(state)

    assert result.context_requirements == ()
    assert result.covered is False
    assert "declared_contract_unusable" in result.notes


# --- AC-37: transition identifiers --------------------------------------------------------------


def test_allowed_function_names_match_safe_eval():
    assert frozenset(ALLOWED_FUNCTIONS) == ALLOWED_FUNCTION_NAMES


# (expression, expected names)
EXPRESSIONS = [
    pytest.param("status == 'ok'", ["status"], id="single-name"),
    pytest.param("pull_request.title != ''", ["pull_request"], id="attribute-root"),
    pytest.param("len(issues) <= 35", ["issues"], id="allowed-function-is-skipped"),
    pytest.param("'done' in keys", [], id="keys-is-skipped"),
    pytest.param("all_done == True", ["all_done"], id="true-constant-is-skipped"),
    pytest.param("flag is None", ["flag"], id="none-constant-is-skipped"),
    pytest.param("data[key] > 1", ["data", "key"], id="subscript-names"),
    pytest.param("sorted(items)[0] == 'a'", ["items"], id="nested-call"),
    pytest.param("not (a and b)", ["a", "b"], id="boolean-operands"),
    pytest.param("[x for x in items]", ["items"], id="comprehension-target-is-local"),
    pytest.param("'literal'", [], id="no-names"),
    pytest.param("status ==", [], id="syntax-error-gives-none"),
    pytest.param("", [], id="empty-expression"),
    pytest.param(None, [], id="not-a-string"),
]


@pytest.mark.parametrize(("expression", "expected"), EXPRESSIONS)
def test_extract_expression_names(expression, expected):
    assert sorted(extract_expression_names(expression)) == sorted(expected)


# (output_schema, next payload, expected (path, name, outcome, reason) rows)
TRANSITIONS = [
    pytest.param(
        SCHEMA_CONTRACT,
        {"condition": {"expression": "doc == 'x'", "then": "a", "otherwise": "b"}},
        [("next.condition.expression", "doc", Outcome.OK, REASON_OK)],
        id="condition-name-in-schema-contract",
    ),
    pytest.param(
        SCHEMA_CONTRACT,
        {"condition": {"expression": "absent == 'x'", "then": "a", "otherwise": "b"}},
        [("next.condition.expression", "absent", Outcome.ERROR, REASON_NOT_IN_OUTPUT_CONTRACT)],
        id="condition-name-missing-from-schema-contract",
    ),
    pytest.param(
        EXAMPLE_CONTRACT,
        {"condition": {"expression": "success == True", "then": "a", "otherwise": "b"}},
        [("next.condition.expression", "success", Outcome.OK, REASON_OK)],
        id="condition-name-in-example-contract",
    ),
    pytest.param(
        RECOVERED_CONTRACT,
        {"condition": {"expression": "all_done == True", "then": "a", "otherwise": "b"}},
        [("next.condition.expression", "all_done", Outcome.OK, REASON_OK)],
        id="condition-name-in-recovered-keys",
    ),
    pytest.param(
        None,
        {"condition": {"expression": "language == 'Python'", "then": "a", "otherwise": "b"}},
        [("next.condition.expression", "language", Outcome.UNVERIFIED, REASON_NO_USABLE_OUTPUT_CONTRACT)],
        id="condition-without-output-contract",
    ),
    pytest.param(
        UNKNOWN_LIST_CONTRACT,
        {"condition": {"expression": "feature == 'x'", "then": "a", "otherwise": "b"}},
        [("next.condition.expression", "feature", Outcome.UNVERIFIED, REASON_NO_USABLE_OUTPUT_CONTRACT)],
        id="condition-on-a-list-without-dict-items",
    ),
    pytest.param(
        LIST_CONTRACT,
        {"condition": {"expression": "document == 'x'", "then": "a", "otherwise": "b"}},
        [("next.condition.expression", "document", Outcome.ERROR, REASON_LIST_OUTPUT)],
        id="condition-on-a-list-output-is-never-ok",
    ),
    pytest.param(
        SCHEMA_CONTRACT,
        {
            "switch": {
                "cases": [
                    {"condition": "doc == 'x'", "state_id": "a"},
                    {"condition": "absent == 'y'", "state_id": "b"},
                ],
                "default": "c",
            }
        },
        [
            ("next.switch.cases[0].condition", "doc", Outcome.OK, REASON_OK),
            ("next.switch.cases[1].condition", "absent", Outcome.ERROR, REASON_NOT_IN_OUTPUT_CONTRACT),
        ],
        id="switch-cases",
    ),
    pytest.param(
        LIST_CONTRACT,
        {"switch": {"cases": [{"condition": "document == 'x'", "state_id": "a"}], "default": "c"}},
        [("next.switch.cases[0].condition", "document", Outcome.ERROR, REASON_LIST_OUTPUT)],
        id="switch-on-a-list-output-is-never-ok",
    ),
    pytest.param(
        SCHEMA_CONTRACT,
        {"state_id": "a", "iter_key": "items"},
        [("next.iter_key", "items", Outcome.OK, REASON_OK)],
        id="iteration-key-declared-array",
    ),
    pytest.param(
        SCHEMA_CONTRACT,
        {"state_id": "a", "iter_key": "doc"},
        [("next.iter_key", "doc", Outcome.WARNING, REASON_ITER_KEY_NOT_ARRAY)],
        id="iteration-key-declared-non-array",
    ),
    pytest.param(
        SCHEMA_CONTRACT,
        {"state_id": "a", "iter_key": "absent"},
        [("next.iter_key", "absent", Outcome.ERROR, REASON_NOT_IN_OUTPUT_CONTRACT)],
        id="iteration-key-missing-from-contract",
    ),
    pytest.param(
        EXAMPLE_CONTRACT,
        {"state_id": "a", "iter_key": "issues"},
        [("next.iter_key", "issues", Outcome.OK, REASON_OK)],
        id="iteration-key-in-example-contract-has-unverified-type",
    ),
    pytest.param(
        None,
        {"state_id": "a", "iter_key": "unused"},
        [("next.iter_key", "unused", Outcome.UNVERIFIED, REASON_NO_USABLE_OUTPUT_CONTRACT)],
        id="iteration-key-without-output-contract",
    ),
    pytest.param(LIST_CONTRACT, {"state_id": "a", "iter_key": "_"}, [], id="iteration-key-on-a-list-output-is-ignored"),
    pytest.param(
        UNKNOWN_LIST_CONTRACT,
        {"state_id": "a", "iter_key": "_"},
        [],
        id="iteration-key-on-a-list-without-dict-items-is-ignored",
    ),
    pytest.param(
        SCHEMA_CONTRACT,
        {"state_id": "a", "iter_key": "/items/0"},
        [("next.iter_key", "items", Outcome.OK, REASON_OK)],
        id="iteration-key-pointer-first-segment",
    ),
    pytest.param(
        SCHEMA_CONTRACT,
        {"state_id": "a", "iter_key": "/absent"},
        [("next.iter_key", "absent", Outcome.ERROR, REASON_NOT_IN_OUTPUT_CONTRACT)],
        id="iteration-key-pointer-missing",
    ),
    pytest.param(SCHEMA_CONTRACT, {"state_id": "a"}, [], id="no-transition-requirements"),
]


@pytest.mark.parametrize(("output_schema", "next_payload", "expected"), TRANSITIONS)
def test_ac37_transition_checks(output_schema, next_payload, expected):
    state = _state(output_schema=output_schema, next=next_payload)

    result = derive_state_requirements(state)
    rows = [
        (check.requirement.path, check.requirement.name, check.outcome, check.reason)
        for check in result.transition_checks
    ]

    assert rows == expected


def _pointer_schema(data: dict) -> str:
    return json.dumps({"type": "object", "properties": {"data": data}, "required": ["data"]})


@pytest.mark.parametrize(
    ("data", "iter_key", "expected"),
    [
        pytest.param({"type": "object"}, "/data/items", (Outcome.OK, REASON_OK), id="nested-type-not-stated"),
        pytest.param(
            {"type": "object", "properties": {"items": {"type": "array"}}},
            "/data/items",
            (Outcome.OK, REASON_OK),
            id="nested-array",
        ),
        pytest.param(
            {"type": "object", "properties": {"items": {"type": "string"}}},
            "/data/items",
            (Outcome.WARNING, REASON_ITER_KEY_NOT_ARRAY),
            id="nested-non-array",
        ),
        pytest.param(
            {"type": "object", "properties": {"items": {"type": "string"}}},
            "/data/items/",
            (Outcome.OK, REASON_OK),
            id="trailing-slash-parses-a-string",
        ),
        pytest.param({"type": "object"}, "/data", (Outcome.WARNING, REASON_ITER_KEY_NOT_ARRAY), id="one-segment"),
        pytest.param({"type": "object"}, "/absent/items", (Outcome.ERROR, REASON_NOT_IN_OUTPUT_CONTRACT), id="missing"),
    ],
)
def test_pointer_iteration_key_is_judged_at_its_full_path(data, iter_key, expected):
    state = _state(output_schema=_pointer_schema(data), next={"state_id": "worker", "iter_key": iter_key})

    result = derive_state_requirements(state)

    assert [(c.outcome, c.reason) for c in result.transition_checks] == [expected]


def test_pointer_iteration_key_is_split_like_the_runtime_pointer():
    data = {"a~1b": [1]}
    schema = json.dumps({"type": "object", "properties": {"a~1b": {"type": "array"}}})
    state = _state(output_schema=schema, next={"state_id": "worker", "iter_key": "/a~1b"})

    result = derive_state_requirements(state)

    assert [(c.requirement.name, c.outcome) for c in result.transition_checks] == [("a~1b", Outcome.OK)]
    assert UnwrappingJsonPointerEvaluator.get_node_by_pointer(data, "/a~1b") == [1]


def test_ac37_transition_requirements_never_cover_a_state():
    state = _state(
        output_schema=SCHEMA_CONTRACT,
        next={"condition": {"expression": "doc == 'x'", "then": "a", "otherwise": "b"}},
    )

    result = derive_state_requirements(state)

    assert result.transition_requirements
    assert result.covered is False
    assert all(item.kind is RecordKind.DERIVED for item in result.transition_requirements)


def test_ac37_transition_requirements_are_separate_from_context_requirements():
    state = _state(
        task="Use {{ report }}",
        resolve_dynamic_values_in_prompt=True,
        output_schema=SCHEMA_CONTRACT,
        next={"state_id": "a", "iter_key": "items"},
    )

    result = derive_state_requirements(state)

    assert _names(result.context_requirements, ORIGIN_PLACEHOLDER) == ["report"]
    assert [item.origin for item in result.transition_requirements] == [ORIGIN_ITER_KEY]


def test_condition_and_switch_origins_are_distinct():
    condition_state = _state(
        output_schema=SCHEMA_CONTRACT,
        next={"condition": {"expression": "doc == 'x'", "then": "a", "otherwise": "b"}},
    )
    switch_state = _state(
        output_schema=SCHEMA_CONTRACT,
        next={"switch": {"cases": [{"condition": "doc == 'x'", "state_id": "a"}], "default": "b"}},
    )

    assert [item.origin for item in derive_state_requirements(condition_state).transition_requirements] == [
        ORIGIN_CONDITION
    ]
    assert [item.origin for item in derive_state_requirements(switch_state).transition_requirements] == [ORIGIN_SWITCH]


# --- failure isolation --------------------------------------------------------------------------

MALFORMED = [
    pytest.param(SimpleNamespace(id="s", next=None, output_schema=None), id="null-next"),
    pytest.param(SimpleNamespace(id="s", next=SimpleNamespace(), output_schema=12), id="scalar-output-schema"),
    pytest.param(SimpleNamespace(), id="empty-object"),
]


@pytest.mark.parametrize("state", MALFORMED)
def test_malformed_states_never_raise(state):
    result = derive_state_requirements(state)

    assert result.context_requirements == ()
    assert result.transition_checks == ()
