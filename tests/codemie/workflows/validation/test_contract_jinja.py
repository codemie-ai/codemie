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

"""EPMCDME-16503-B: occurrence-sensitive Jinja derivation and real-renderer checks."""

import time
from unittest.mock import Mock

import pytest
import yaml
from jinja2.defaults import DEFAULT_FILTERS, DEFAULT_NAMESPACE

from codemie.core.workflow_models import WorkflowConfig
from codemie.core.workflow_models.workflow_models import WorkflowState, WorkflowTool
from codemie.service.tools.dynamic_value_utils import process_string
from codemie.workflows.contracts.derive import (
    ORIGIN_PLACEHOLDER,
    ORIGIN_TOOL_BINDING_TEMPLATE,
    derive_state_requirements,
)
from codemie.workflows.contracts.jinja_analysis import extract_context_names
from codemie.workflows.contracts.linker import link_workflow_contracts
from codemie.workflows.contracts.models import Outcome, RecordKind, ReportedAs


def _render(source: str, **context) -> str:
    # The sentinel forces the real renderer through its non-empty-context path.
    return process_string(source, {"sentinel": "present", **context}, enable_recursive_resolution=False)


VALID_LOCALS = [
    pytest.param("{% macro show(person) %}{{ person }}{% endmacro %}{{ show('Ada') }}", "Ada", id="macro"),
    pytest.param("{% with person='Ada' %}{{ person }}{% endwith %}", "Ada", id="with"),
    pytest.param("{% for (name, value) in [('Ada', 2)] %}{{ name }}={{ value }}{% endfor %}", "Ada=2", id="tuple-loop"),
    pytest.param("{{ lipsum }}", str(DEFAULT_NAMESPACE["lipsum"]), id="global"),
]

LATE_BOUND_MACRO = (
    "{{ person | default('') }}{% macro show() %}{{ person }}{% endmacro %}" "{% set person='Ada' %}{{ show() }}"
)


@pytest.mark.parametrize(
    ("source", "requirements", "expected"),
    [
        pytest.param(LATE_BOUND_MACRO, (), "Ada", id="called-after-assignment"),
        pytest.param(
            "{{ person | default('') }}{% macro show() %}{{ person }}{% endmacro %}"
            "{{ show() }}{% set person='Ada' %}",
            ("person",),
            "",
            id="called-before-assignment",
        ),
        pytest.param(
            "{{ person | default('') }}{% macro show() %}{{ person }}{% endmacro %}"
            "{{ show() }}{% set person='Ada' %}{{ show() }}",
            ("person",),
            "Ada",
            id="called-before-and-after-assignment",
        ),
        pytest.param(
            "{{ person | default('') }}{% macro show() %}{{ person }}{% endmacro %}"
            "{% set alias=show %}{% set person='Ada' %}{{ alias() }}",
            (),
            "Ada",
            id="known-alias",
        ),
        pytest.param(
            "{{ person | default('') }}{% macro outer() %}"
            "{% macro inner() %}{{ person }}{% endmacro %}{{ inner() }}{% endmacro %}"
            "{% set person='Ada' %}{{ outer() }}",
            (),
            "Ada",
            id="nested-macro",
        ),
        pytest.param(
            "{% macro show() %}{{ person }}{% endmacro %}{{ show() }}",
            ("person",),
            "",
            id="genuine-free-macro-name",
        ),
        pytest.param(
            "{{ person | default('') }}{% macro show() %}{{ person }}{% endmacro %}"
            "{% set person='Ada' %}{{ {'show': show}['show']() }}",
            ("person",),
            "Ada",
            id="dynamic-call-keeps-uncertainty",
        ),
    ],
)
def test_macro_context_is_checked_at_known_calls(source, requirements, expected):
    assert extract_context_names(source) == requirements
    assert _render(source) == expected


def test_a_macro_called_before_assignment_reads_the_supplied_context():
    source = "{{ person | default('') }}{% macro show() %}{{ person }}{% endmacro %}{{ show() }}{% set person='Ada' %}"
    assert extract_context_names(source) == ("person",)
    assert _render(source, person="context") == "contextcontext"


@pytest.mark.parametrize("mode", ["warn", "strict"])
def test_full_linker_accepts_a_macro_called_after_its_context_binding(mode):
    report = link_workflow_contracts(_workflow(LATE_BOUND_MACRO, mode))
    assert not [record for record in report.records if record.kind is RecordKind.DERIVED and record.path == "task"]
    assert report.blocking_records() == []
    assert _render(LATE_BOUND_MACRO) == "Ada"


@pytest.mark.parametrize(
    "source",
    [
        "{{ missing }}{% block x %}a{% endblock %}{% block x %}b{% endblock %}",
        "{{ missing }}{% for loop in [1] %}{{ loop }}{% endfor %}",
        "{{ missing }}{% macro show(x, x) %}{{ x }}{% endmacro %}{{ show(1, 2) }}",
    ],
)
def test_invalid_jinja_declarations_use_the_runtime_raw_template_fallback(source):
    assert extract_context_names(source) == ()
    assert _render(source) == source


@pytest.mark.parametrize(("source", "expected"), VALID_LOCALS)
def test_valid_local_templates_have_no_context_requirements_and_render(source, expected):
    assert extract_context_names(source) == ()
    assert _render(source) == expected


@pytest.mark.parametrize("name", sorted(DEFAULT_NAMESPACE))
def test_all_runtime_environment_globals_are_available_without_context(name):
    source = "{{ " + name + " }}"
    assert extract_context_names(source) == ()
    assert _render(source) == str(DEFAULT_NAMESPACE[name])


@pytest.mark.parametrize(
    ("source", "context", "expected", "requirements"),
    [
        pytest.param("{{ person }}", {"person": "Ada"}, "Ada", ("person",), id="free-name"),
        pytest.param("{{ person.name }}", {"person": {"name": "Ada"}}, "Ada", ("person",), id="free-dotted"),
        pytest.param(
            "{{ person }}{% set person='local' %}{{ person }}",
            {"person": "Ada"},
            "Adalocal",
            ("person",),
            id="before-set",
        ),
        pytest.param("{% set person='local' %}{{ person }}", {"person": "Ada"}, "local", (), id="after-set"),
        pytest.param("{% set one, two='A', 'B' %}{{ one }}{{ two }}", {}, "AB", (), id="multiple-set-targets"),
        pytest.param("{% set (one, two)='A', 'B' %}{{ one }}{{ two }}", {}, "AB", (), id="parenthesized-set-targets"),
        pytest.param(
            "{{ person }}{% with person='local' %}{{ person }}{% endwith %}{{ person }}",
            {"person": "Ada"},
            "AdalocalAda",
            ("person",),
            id="with-before-after",
        ),
        pytest.param(
            "{% set person='outer' %}{% with person='inner' %}{{ person }}{% with person='nested' %}{{ person }}{% endwith %}{{ person }}{% endwith %}{{ person }}",
            {},
            "innernestedinnerouter",
            (),
            id="nested-shadowing",
        ),
        pytest.param(
            "{% for person in ['local'] %}{{ person }}{% endfor %}{{ person }}",
            {"person": "Ada"},
            "localAda",
            ("person",),
            id="loop-does-not-leak",
        ),
        pytest.param(
            "{{ person | upper }}{% for person in ['local'] %}{{ person }}{% endfor %}",
            {"person": "Ada"},
            "ADAlocal",
            (),
            id="filtered-free-and-loop-local",
        ),
        pytest.param(
            "{{ person | upper }}{% macro show(person) %}{{ person }}{% endmacro %}{{ show('local') }}",
            {"person": "Ada"},
            "ADAlocal",
            (),
            id="filtered-free-and-macro-local",
        ),
        pytest.param(
            "{{ person | upper }}{% with person='local' %}{{ person }}{% endwith %}",
            {"person": "Ada"},
            "ADAlocal",
            (),
            id="filtered-free-and-with-local",
        ),
        pytest.param(
            "{% for person in ['local'] %}{% set alias=person %}{{ alias }}{% endfor %}{{ alias }}",
            {"alias": "outside"},
            "localoutside",
            ("alias",),
            id="loop-assignment-scope",
        ),
        pytest.param(
            "{% for person in ['local'] %}{{ later }}{% endfor %}{% set later='value' %}",
            {"later": "context"},
            "",
            (),
            id="outer-local-before-assignment",
        ),
        pytest.param(
            "{% macro show() %}{{ later }}{% endmacro %}{% set later='value' %}{{ show() }}",
            {"later": "context"},
            "value",
            (),
            id="macro-captures-outer-local",
        ),
        pytest.param(
            "{% if flag %}{% set person='local' %}{% endif %}{{ person }}",
            {"flag": False, "person": "Ada"},
            "Ada",
            ("person",),
            id="conditional-assignment",
        ),
        pytest.param(
            "{% if flag %}{% set person='A' %}{% else %}{% set person='B' %}{% endif %}{{ person }}",
            {"flag": False, "person": "Ada"},
            "B",
            (),
            id="all-branches-assign",
        ),
        pytest.param(
            "{% if flag %}{% set person='A' %}{% elif other %}{% set person='B' %}{% else %}{% set person='C' %}{% endif %}{{ person }}",
            {"flag": False, "other": True},
            "B",
            (),
            id="elif-assigns",
        ),
        pytest.param(
            "{% set person %}{{ free }}{% endset %}{{ person }}", {"free": "Ada"}, "Ada", ("free",), id="capture-scope"
        ),
        pytest.param(
            "{% macro wrapper() %}{{ caller('Ada') }}{% endmacro %}{% call(name) wrapper() %}{{ name }}{% endcall %}",
            {},
            "Ada",
            (),
            id="call-block-parameter",
        ),
        pytest.param(
            "{% macro show() %}{{ kwargs }}{{ varargs }}{% endmacro %}{{ show(1, value=2) }}",
            {},
            "{'value': 2}(1,)",
            (),
            id="macro-implicit-parameters",
        ),
        pytest.param(
            "{% filter upper %}{% set person='local' %}{{ person }}{% endfilter %}{{ person }}",
            {"person": "Ada"},
            "LOCALAda",
            ("person",),
            id="filter-block-scope",
        ),
        pytest.param(
            "{% autoescape true %}{% set person='local' %}{{ person }}{% endautoescape %}{{ person }}",
            {"person": "Ada"},
            "localAda",
            ("person",),
            id="autoescape-scope",
        ),
        pytest.param(
            "{% for person in ['Ada'] %}{% block body scoped %}{{ person }}{% endblock %}{% endfor %}",
            {},
            "Ada",
            (),
            id="scoped-block",
        ),
        pytest.param(
            "{% for person in ['local'] %}{% block body %}{{ person }}{% endblock %}{% endfor %}",
            {"person": "Ada"},
            "Ada",
            ("person",),
            id="unscoped-block",
        ),
        pytest.param(
            "{% set person='local' %}{% block body %}{{ person }}{% endblock %}",
            {"person": "Ada"},
            "local",
            (),
            id="unscoped-block-uses-root-assignment",
        ),
        pytest.param(
            "{% for person in [] %}{{ person }}{% else %}{{ person }}{% endfor %}",
            {"person": "outside"},
            "outside",
            ("person",),
            id="loop-else-outer-name",
        ),
        pytest.param(
            "{% for x in ['A'] %}{% for x in ['B'] %}{{ x }}{{ loop.index }}{% endfor %}{{ x }}{% endfor %}",
            {},
            "B1A",
            (),
            id="nested-loop",
        ),
        pytest.param("{{ loop.index }}", {"loop": {"index": 7}}, "7", ("loop",), id="loop-name-outside-loop"),
        pytest.param(
            "{# {{ fake }} #}{% raw %}{{ literal }}{% endraw %}{{ real }}",
            {"real": "Ada"},
            "{{ literal }}Ada",
            ("real",),
            id="raw-and-comment",
        ),
        pytest.param("a {{- person -}} b", {"person": "Ada"}, "aAdab", ("person",), id="whitespace-control"),
        pytest.param(
            "{{ second }}{{ first.name }}{{ second.value }}",
            {"second": {"value": "B"}, "first": {"name": "A"}},
            "{'value': 'B'}AB",
            ("second", "first"),
            id="ordered-deduplication",
        ),
    ],
)
def test_scope_occurrences_match_real_rendering(source, context, expected, requirements):
    assert extract_context_names(source) == requirements
    assert _render(source, **context) == expected


def test_free_name_presence_and_dotted_missing_fallback():
    assert extract_context_names("{{ person }}") == ("person",)
    assert _render("{{ person }}", person="Ada") == "Ada"
    assert _render("{{ person }}") == ""
    assert extract_context_names("{{ person.name }}") == ("person",)
    assert _render("{{ person.name }}", person={"name": "Ada"}) == "Ada"
    assert _render("{{ person.name }}") == "{{ person.name }}"


@pytest.mark.parametrize(
    "source", ["{{ missing", "{% if flag %}{{ missing }}", "{% unknown %}{{ missing }}", "{{ value. }}"]
)
def test_parse_failure_keeps_raw_template_and_adds_no_requirement(source):
    assert extract_context_names(source) == ()
    assert _render(source) == source


@pytest.mark.parametrize(
    ("source", "context", "expected"),
    [
        ("{{ absent | default('fallback') }}", {}, "fallback"),
        ("{{ person | upper }}", {"person": "Ada"}, "ADA"),
        ("{{ range(2) | list }}", {}, "[0, 1]"),
        ("{{ person['name'] }}", {"person": {"name": "Ada"}}, "Ada"),
        ("{{ items[0].name }}", {"items": [{"name": "Ada"}]}, "Ada"),
        ("{% if flag %}yes{% endif %}", {"flag": True}, "yes"),
        ("{% set person = free %}{{ person }}", {"free": "Ada"}, "Ada"),
        ("{{ left ~ right }}", {"left": "A", "right": "B"}, "AB"),
    ],
)
def test_excluded_expressions_remain_excluded(source, context, expected):
    assert extract_context_names(source) == ()
    assert _render(source, **context) == expected


def test_static_analysis_never_calls_template_functions(monkeypatch):
    callback = Mock(side_effect=AssertionError("static analysis must not render"))
    monkeypatch.setitem(DEFAULT_NAMESPACE, "callback", callback)
    monkeypatch.setitem(DEFAULT_FILTERS, "callback", callback)
    assert extract_context_names("{{ callback() }} {{ 'value' | callback }} {{ actual }}") == ("actual",)
    callback.assert_not_called()


@pytest.mark.parametrize(
    ("source", "names"),
    [
        ("{% with local='Ada' %}{{ local }}{% endwith %}{{ owner.name }}", ("owner",)),
        ("{{ person | upper }}{% for person in ['local'] %}{{ person }}{% endfor %}", ()),
        ("{% macro show(person) %}{{ person }}{% endmacro %}{{ show('Ada') }}{{ actual }}", ("actual",)),
    ],
)
def test_agent_and_tool_template_callers_use_the_same_analysis(source, names):
    agent = WorkflowState(
        id="agent", assistant_id="a1", task=source, resolve_dynamic_values_in_prompt=True, next={"state_id": "end"}
    )
    tool_state = WorkflowState(id="tool", tool_id="t1", tool_args={"argument": source}, next={"state_id": "end"})
    tool = WorkflowTool(id="t1", tool="generic_tool")
    agent_result = derive_state_requirements(agent)
    tool_result = derive_state_requirements(tool_state, tool=tool, mandatory_parameters=frozenset({"argument"}))
    assert tuple(req.name for req in agent_result.context_requirements if req.origin == ORIGIN_PLACEHOLDER) == names
    assert (
        tuple(req.name for req in tool_result.context_requirements if req.origin == ORIGIN_TOOL_BINDING_TEMPLATE)
        == names
    )


def _workflow(source: str, mode: str) -> WorkflowConfig:
    schema = '{"type":"object","properties":{"person":{"type":"string"}},"required":["person"]}'
    config = WorkflowConfig(
        id="jinja-regression",
        name="jinja-regression",
        description="Jinja scope regression",
        project="test",
        mode="Sequential",
        yaml_config=yaml.safe_dump(
            {
                "save_validation": mode,
                "assistants": [{"id": "a1", "system_prompt": "test"}],
                "states": [
                    {
                        "id": "producer",
                        "assistant_id": "a1",
                        "output_schema": schema,
                        "next": {"state_id": "consumer", "reset_keys_in_context_store": ["person"]},
                    },
                    {
                        "id": "consumer",
                        "assistant_id": "a1",
                        "task": source,
                        "resolve_dynamic_values_in_prompt": True,
                        "next": {"state_id": "end"},
                    },
                ],
            }
        ),
    )
    config.parse_execution_config()
    return config


@pytest.mark.parametrize("mode", ["warn", "strict"])
@pytest.mark.parametrize(("source", "expected"), VALID_LOCALS)
def test_full_linker_does_not_report_valid_local_templates(mode, source, expected):
    report = link_workflow_contracts(_workflow(source, mode))
    assert not [record for record in report.records if record.kind is RecordKind.DERIVED and record.path == "task"]
    assert report.blocking_records() == []
    assert _render(source) == expected


@pytest.mark.parametrize(("mode", "reported_as"), [("warn", ReportedAs.WARNING), ("strict", ReportedAs.BLOCKS)])
def test_full_linker_still_warns_or_blocks_a_missing_supported_name(mode, reported_as):
    report = link_workflow_contracts(_workflow("{{ person }}", mode))
    records = [record for record in report.records if record.kind is RecordKind.DERIVED and record.path == "task"]
    assert len(records) == 1
    assert records[0].outcome is Outcome.ERROR
    assert records[0].reported_as is reported_as


def _double_call_chain(levels: int, *, between: str = "") -> str:
    """Every macro calls the previous one twice, so walking each call doubles the work per level (MR !4725 R2).

    `between` goes between the two calls; a `{% set %}` there makes the two calls see different bindings.
    """
    lines = ["{% macro m0() %}{{ topic }}{% endmacro %}"]
    lines += [
        f"{{% macro m{i}() %}}{{{{ m{i - 1}() }}}}{between.format(i=i)}{{{{ m{i - 1}() }}}}{{% endmacro %}}"
        for i in range(1, levels + 1)
    ]
    lines.append(f"Write one line about {{{{ m{levels}() }}}}")
    return "\n".join(lines)


def test_two_level_macro_chain_reads_topic():
    assert extract_context_names(_double_call_chain(2)) == ("topic",)


def test_a_repeated_macro_call_is_walked_once():
    from codemie.workflows.contracts import jinja_analysis

    walks = Mock(wraps=jinja_analysis._OutputReads._scope)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(jinja_analysis._OutputReads, "_scope", lambda self, *a, **k: walks(self, *a, **k))
        started = time.perf_counter()
        assert extract_context_names(_double_call_chain(30)) == ("topic",)
        elapsed = time.perf_counter() - started

    assert walks.call_count <= 31
    assert elapsed < 1.0


def test_a_template_past_the_walk_cap_is_not_analysed():
    from codemie.workflows.contracts.jinja_analysis import TemplateAnalysisCapReached

    with pytest.raises(TemplateAnalysisCapReached):
        extract_context_names(_double_call_chain(30, between="{{% set x{i} = 1 %}}"))


def test_the_linker_reports_a_template_past_the_walk_cap_as_not_verified():
    source = _double_call_chain(30, between="{{% set x{i} = 1 %}}")
    started = time.perf_counter()
    report = link_workflow_contracts(_workflow(source, "strict"))
    elapsed = time.perf_counter() - started

    assert report.cap_reached is True
    records = [record for record in report.records if record.path == "task"]
    assert [(record.kind, record.outcome) for record in records] == [(RecordKind.ANALYSIS_FAILURE, Outcome.UNVERIFIED)]
    assert elapsed < 5.0
