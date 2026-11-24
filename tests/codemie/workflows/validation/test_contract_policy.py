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

"""Mode policy, coverage, failure isolation and messages (EPMCDME-16503, T7).

One case set per AC: AC-17 contract form under workflow and state strict, AC-18 default mode, AC-19 strict, AC-20
state-level modes, AC-21 coverage, AC-22 rejections (records whose `reported_as` blocks; raw `outcome == ERROR` is
only counted by AC-43), plus the message rules: fixed templates, sanitised and capped names, no echoed values.
"""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from codemie.core.workflow_models.workflow_config import WorkflowConfigTemplate
from codemie.core.workflow_models.workflow_models import WorkflowState, WorkflowTool
from codemie.workflows.constants import ContractValidationMode
from codemie.workflows.contracts import derive, simulate_model
from codemie.workflows.contracts import policy as policy_module
from codemie.workflows.contracts import effects as effects_module
from codemie.workflows.contracts.models import (
    ContractAnalysisStatus,
    Outcome,
    RecordKind,
    ReportedAs,
    StrictSource,
)
from codemie.workflows.contracts.policy import (
    CAUSE_POLICY_FAILED,
    CAUSE_SETTINGS_FAILED,
    CAUSE_SIMULATION_FAILED,
    LABEL_CERTAIN_FAILURE,
    LABEL_NOT_GUARANTEED,
    MESSAGE_TEMPLATES,
    build_report,
    failure_report,
    resolve_state_modes,
    safe_name,
)
from codemie.workflows.contracts.settings import ContractSettings
from codemie.workflows.contracts.simulate import simulate_workflow
from codemie.workflows.contracts.simulate_model import (
    Finding,
    SimulationResult,
)

TEMPLATES_DIR = Path(__file__).resolve().parents[4] / "config" / "templates" / "workflow"

WARN = ContractValidationMode.WARN
STRICT = ContractValidationMode.STRICT


def _schema(types: dict[str, str]) -> str:
    return json.dumps(
        {
            "type": "object",
            "properties": {name: {"type": kind} for name, kind in types.items()},
            "required": list(types),
        }
    )


DOC_SCHEMA = _schema({"doc": "string"})
EXAMPLE_DOC = '{"doc": "the document text"}'
RECOVERABLE_DOC = '{"doc": "the document text",}'


def _state(state_id: str, next_: dict | None = None, **overrides) -> WorkflowState:
    data: dict = {"id": state_id, "assistant_id": "assistant-1", "next": next_ or {}}
    data.update(overrides)
    return WorkflowState(**data)


def _needs(state_id: str, types: dict[str, str], next_: dict | None = None, **overrides) -> WorkflowState:
    return _state(state_id, next_, input_schema=_schema(types), **overrides)


def _entry(next_id: str) -> WorkflowState:
    """An entry state declaring an empty input contract and a known empty output: every source stays known, and
    nothing in the workflow supplies a field (the entry's own declared input is the workflow input, AC-2)."""
    empty = json.dumps({"type": "object", "properties": {}})
    return _state("entry", {"state_id": next_id}, input_schema=empty, output_schema=empty)


def _tool_state(state_id: str, next_: dict | None = None, **overrides) -> WorkflowState:
    data: dict = {"id": state_id, "tool_id": "tool-1", "next": next_ or {}}
    data.update(overrides)
    return WorkflowState(**data)


def _report(states, *, mode=None, require=False, tools=None, resolver=None, known_input=False):
    """Simulate and apply the policy. `known_input=False` makes every source known, which is what produces ERRORs."""
    result = simulate_workflow(states, tools=tools, resolver=resolver, unknown_initial_input=not known_input)
    settings = ContractSettings(mode=mode or WARN, require_input_contracts=require)
    return build_report(result, states=states, settings=settings)


def _all_known(states, **kwargs):
    return _report(states, known_input=True, **kwargs)


def _row(report, state_id: str, path: str):
    rows = [r for r in report.records if r.consumer_state_id == state_id and r.path == path]
    assert rows, f"no record at {state_id}:{path}; have {[(r.consumer_state_id, r.path) for r in report.records]}"
    return rows[0]


def _outcome_class(report, state_id: str, path: str) -> tuple[str, str]:
    record = _row(report, state_id, path)
    return record.outcome.value, record.reported_as.value


class _FakeResolver:
    def __init__(self, mandatory):
        self.mandatory = mandatory

    def mandatory_params(self, tool):
        return self.mandatory.get(tool.tool)


# --- AC-18: default mode --------------------------------------------------------------------------


def test_ac18_declared_error_rejects_in_default_mode():
    report = _all_known([_entry("a"), _needs("a", {"doc": "string"})])

    assert _outcome_class(report, "a", "input_schema.doc") == ("ERROR", "blocks")
    assert len(report.blocking_records()) == 1


def test_ac18_derived_placeholder_error_saves_with_the_renders_empty_label():
    states = [_state("a", resolve_dynamic_values_in_prompt=True, task="Summarise {{ doc }}")]

    report = _all_known(states)

    record = _row(report, "a", "task")
    assert (record.outcome, record.reported_as) == (Outcome.ERROR, ReportedAs.WARNING)
    assert record.label == derive.LABEL_RENDERS_EMPTY
    assert report.blocking_records() == []


def test_ac18_derived_not_guaranteed_kind_is_marked_accordingly():
    states = [
        _state(
            "a",
            {"condition": {"expression": "status == 'ok'", "then": "b", "otherwise": "b"}},
            output_schema=_schema({"other": "string"}),
        ),
        _state("b"),
    ]

    report = _report(states)

    record = _row(report, "a", "next.condition.expression")
    assert (record.outcome, record.reported_as, record.label) == (
        Outcome.ERROR,
        ReportedAs.WARNING,
        LABEL_NOT_GUARANTEED,
    )


def test_ac18_derived_mandatory_parameter_is_marked_certain_runtime_failure():
    tools = [WorkflowTool(id="tool-1", tool="searcher")]
    resolver = _FakeResolver({"searcher": frozenset({"url", "query"})})

    report = _report([_tool_state("a", tool_args={"url": "literal"})], tools=tools, resolver=resolver)

    record = _row(report, "a", "tool_args.query")
    assert (record.outcome, record.reported_as, record.label) == (
        Outcome.ERROR,
        ReportedAs.WARNING,
        LABEL_CERTAIN_FAILURE,
    )


def test_ac18_derived_requirements_are_evaluated_even_with_a_declared_contract():
    states = [
        _entry("a"),
        _state("a", input_schema=_schema({"seed": "string"}), resolve_dynamic_values_in_prompt=True, task="{{ doc }}"),
    ]

    report = _all_known(states)

    assert _row(report, "a", "task").outcome is Outcome.ERROR
    assert _row(report, "a", "input_schema.seed").reported_as is ReportedAs.BLOCKS


@pytest.mark.parametrize(
    ("states", "path", "kwargs", "expected"),
    [
        # WARNING -> warning
        (
            [
                _state("a", {"condition": {"expression": "True", "then": "b", "otherwise": "c"}}),
                _state("b", {"state_id": "d"}, output_schema=DOC_SCHEMA),
                _state("c", {"state_id": "d"}, output_schema=_schema({"x": "string"})),
                _needs("d", {"doc": "string"}),
            ],
            "input_schema.doc",
            {"known_input": True},
            ("WARNING", "warning"),
        ),
        # UNVERIFIED -> info
        ([_tool_state("a")], "tool_args", {"tools": [WorkflowTool(id="tool-1", tool="g")]}, ("UNVERIFIED", "info")),
        # OK -> info
        (
            [_state("a", {"state_id": "b"}, output_schema=DOC_SCHEMA), _needs("b", {"doc": "string"})],
            "input_schema.doc",
            {},
            ("OK", "info"),
        ),
    ],
    ids=["warning", "unverified", "ok"],
)
def test_ac18_other_outcomes_in_default_mode(states, path, kwargs, expected):
    report = _report(states, **kwargs)

    state_id = "d" if len(states) == 4 else states[-1].id
    assert _outcome_class(report, state_id, path) == expected


# --- AC-19: workflow strict -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("states", "path", "kwargs", "expected"),
    [
        ([_tool_state("a")], "tool_args", {"tools": [WorkflowTool(id="tool-1", tool="g")]}, ("UNVERIFIED", "blocks")),
        (
            [_state("a", resolve_dynamic_values_in_prompt=True, task="{{ doc }}")],
            "task",
            {"known_input": True},
            ("ERROR", "blocks"),
        ),
        (
            [
                _state("a", {"condition": {"expression": "True", "then": "b", "otherwise": "c"}}),
                _state("b", {"state_id": "d"}, output_schema=DOC_SCHEMA),
                _state("c", {"state_id": "d"}, output_schema=_schema({"x": "string"})),
                _needs("d", {"doc": "string"}),
            ],
            "input_schema.doc",
            {"known_input": True},
            ("WARNING", "blocks"),
        ),
    ],
    ids=["unverified", "error", "warning"],
)
def test_ac19_strict_rejects_every_problem_outcome(states, path, kwargs, expected):
    report = _report(states, mode=STRICT, **kwargs)

    state_id = "d" if len(states) == 4 else "a"
    assert _outcome_class(report, state_id, path) == expected


def test_ac19_strict_keeps_ok_records_as_info():
    states = [
        _state("a", {"state_id": "b"}, output_schema=DOC_SCHEMA),
        _needs("b", {"doc": "string"}),
    ]

    report = _report(states, mode=STRICT)

    assert _outcome_class(report, "b", "input_schema.doc") == ("OK", "info")


def test_ac19_strict_does_not_reject_the_convert_to_schema_advisory():
    report = _report([_state("a", output_schema=RECOVERABLE_DOC)], mode=STRICT)

    advisory = [r for r in report.records if r.kind is RecordKind.CONTRACT_FORM and r.outcome is Outcome.UNVERIFIED]
    assert advisory
    assert {r.reported_as for r in advisory} == {ReportedAs.INFO}


def test_every_record_has_a_reported_as_after_the_policy():
    states = [_tool_state("a", {"state_id": "b"}), _needs("b", {"doc": "string"})]

    report = _report(states, mode=STRICT, tools=[WorkflowTool(id="tool-1", tool="g")])

    assert report.records
    assert all(record.reported_as is not None for record in report.records)


# --- AC-20: state-level modes ---------------------------------------------------------------------


def test_ac20_state_strict_governs_only_its_own_requirements():
    states = [
        _tool_state("a", {"state_id": "b"}),
        _tool_state("b", save_validation="strict"),
    ]

    report = _report(states, tools=[WorkflowTool(id="tool-1", tool="g")])

    assert _row(report, "a", "tool_args").reported_as is ReportedAs.INFO
    assert _row(report, "b", "tool_args").reported_as is ReportedAs.BLOCKS


def test_ac20_state_warn_inside_a_strict_workflow_uses_the_default_rules():
    states = [
        _tool_state("a", {"state_id": "b"}),
        _tool_state("b", save_validation="warn"),
    ]

    report = _report(states, mode=STRICT, tools=[WorkflowTool(id="tool-1", tool="g")])

    assert _row(report, "a", "tool_args").reported_as is ReportedAs.BLOCKS
    assert _row(report, "b", "tool_args").reported_as is ReportedAs.INFO


def test_ac20_state_strict_declared_error_still_blocks_and_derived_error_now_blocks():
    states = [_state("a", save_validation="strict", resolve_dynamic_values_in_prompt=True, task="{{ doc }}")]

    assert _outcome_class(_all_known(states), "a", "task") == ("ERROR", "blocks")


@pytest.mark.parametrize("value", ["foo", "off", 1, {"a": 1}, None])
def test_ac20_unrecognised_state_value_inherits_the_workflow_mode(value):
    states = [WorkflowState.model_construct(id="a", assistant_id="x", next=None, save_validation=value)]

    modes = resolve_state_modes(states, ContractSettings(mode=STRICT))

    assert modes == {"a": STRICT}


def test_ac20_effective_modes_are_listed_per_state_in_the_report():
    states = [_state("a"), _state("b", save_validation="strict"), _state("c", save_validation="warn")]

    report = _report(states, mode=WARN)

    assert report.effective_modes == {"a": WARN, "b": STRICT, "c": WARN}


# --- AC-17: contract form under strict -------------------------------------------------------------


def test_ac17_default_mode_never_rejects_an_example_form_producer():
    states = [
        _state("a", {"state_id": "b"}, output_schema=EXAMPLE_DOC),
        _needs("b", {"doc": "string"}),
    ]

    report = _report(states)

    assert report.blocking_records() == []


def test_ac17_workflow_strict_puts_an_error_on_the_non_schema_form_producer():
    states = [
        _state("a", {"state_id": "b"}, output_schema=EXAMPLE_DOC),
        _needs("b", {"doc": "string"}),
    ]

    report = _report(states, mode=STRICT)

    errors = [r for r in report.records if r.kind is RecordKind.CONTRACT_FORM and r.outcome is Outcome.ERROR]
    assert [(r.consumer_state_id, r.reported_as) for r in errors] == [("a", ReportedAs.BLOCKS)]
    assert "'a'" in errors[0].message


def test_ac17_workflow_strict_accepts_a_schema_form_producer():
    states = [
        _state("a", {"state_id": "b"}, output_schema=DOC_SCHEMA),
        _needs("b", {"doc": "string"}),
    ]

    report = _report(states, mode=STRICT)

    assert not [r for r in report.records if r.kind is RecordKind.CONTRACT_FORM and r.outcome is Outcome.ERROR]
    assert report.blocking_records() == []


def test_ac17_transform_meets_strict_through_its_own_schema():
    from codemie.core.workflow_models.workflow_models import CustomWorkflowNode

    node = CustomWorkflowNode(
        id="t1",
        custom_node_id="transform_node",
        config={
            "mappings": [{"type": "constant", "value": "x", "output_field": "doc"}],
            "output_schema": {"type": "object", "properties": {"doc": {"type": "string"}}},
        },
    )
    states = [WorkflowState(id="a", custom_node_id="t1", next={"state_id": "b"}), _needs("b", {"doc": "string"})]
    result = simulate_workflow(states, custom_nodes=[node])

    report = build_report(result, states=states, settings=ContractSettings(mode=STRICT))

    assert not [r for r in report.records if r.kind is RecordKind.CONTRACT_FORM and r.outcome is Outcome.ERROR]


def test_ac17_state_strict_puts_the_error_on_the_consuming_state_naming_the_producer():
    states = [
        _state("a", {"state_id": "b"}, output_schema=EXAMPLE_DOC),
        _needs("b", {"doc": "string"}, save_validation="strict"),
    ]

    report = _report(states)

    errors = [r for r in report.records if r.kind is RecordKind.CONTRACT_FORM and r.outcome is Outcome.ERROR]
    assert [(r.consumer_state_id, r.reported_as) for r in errors] == [("b", ReportedAs.BLOCKS)]
    assert errors[0].provenance.producers == ("a",)
    assert "'a'" in errors[0].message


def _form_errors(report):
    return [r for r in report.records if r.kind is RecordKind.CONTRACT_FORM and r.outcome is Outcome.ERROR]


@pytest.mark.parametrize("contract", [EXAMPLE_DOC, '[{"doc": "x"}]', '{"doc": "x", // comment\n "n": 1,}'])
def test_ac17_workflow_strict_flags_a_declared_non_schema_producer_that_feeds_nobody(contract):
    states = [_state("a", {"state_id": "b"}, output_schema=contract), _state("b")]

    report = _report(states, mode=STRICT)

    assert [(r.consumer_state_id, r.reported_as) for r in _form_errors(report)] == [("a", ReportedAs.BLOCKS)]


def test_ac17_workflow_strict_does_not_flag_a_producer_that_declares_no_output_contract():
    states = [_state("a", {"state_id": "b"}), _state("b")]

    report = _report(states, mode=STRICT)

    assert not _form_errors(report)


def test_ac17_workflow_strict_reports_a_consumed_non_schema_producer_once():
    states = [
        _state("a", {"state_id": "b"}, output_schema=EXAMPLE_DOC),
        _needs("b", {"doc": "string"}),
    ]

    report = _report(states, mode=STRICT)

    assert [r.consumer_state_id for r in _form_errors(report)] == ["a"]


def test_ac17_workflow_strict_skips_a_non_schema_producer_whose_own_mode_is_not_strict():
    states = [_state("a", {"state_id": "b"}, output_schema=EXAMPLE_DOC, save_validation="warn"), _state("b")]

    report = _report(states, mode=STRICT)

    assert not _form_errors(report)


def test_ac17_state_strict_override_alone_does_not_flag_an_unconsumed_producer():
    states = [_state("a", {"state_id": "b"}, output_schema=EXAMPLE_DOC), _state("b", save_validation="strict")]

    report = _report(states)

    assert not _form_errors(report)


# --- AC-21: coverage ------------------------------------------------------------------------------


def test_ac21_uncovered_node_is_only_listed_when_the_setting_is_off():
    report = _report([_state("a", {"state_id": "b"}), _state("b")])

    assert report.uncovered_state_ids == ("a", "b")
    assert not [r for r in report.records if r.kind is RecordKind.COVERAGE]
    assert report.blocking_records() == []


@pytest.mark.parametrize("mode", [WARN, STRICT])
def test_ac21_uncovered_reachable_node_is_an_error_that_rejects_in_both_modes(mode):
    states = [_needs("a", {"seed": "string"}, {"state_id": "b"}), _state("b")]

    report = _report(states, mode=mode, require=True)

    coverage = [r for r in report.records if r.kind is RecordKind.COVERAGE]
    assert [(r.consumer_state_id, r.outcome, r.reported_as) for r in coverage] == [
        ("b", Outcome.ERROR, ReportedAs.BLOCKS)
    ]


def test_ac21_state_level_warn_does_not_excuse_a_missing_contract():
    report = _report([_state("a", save_validation="warn")], require=True)

    assert [r.reported_as for r in report.records if r.kind is RecordKind.COVERAGE] == [ReportedAs.BLOCKS]


def test_ac21_covered_and_unreachable_nodes_get_no_coverage_record():
    states = [_needs("a", {"seed": "string"}), _state("orphan")]

    report = _report(states, require=True)

    assert not [r for r in report.records if r.kind is RecordKind.COVERAGE]
    assert report.unreachable_state_ids == ("orphan",)
    assert (report.validated_count, report.reachable_total) == (1, 1)


def test_ac21_transition_requirements_do_not_cover_the_node():
    states = [
        _state(
            "a",
            {"condition": {"expression": "status == 'ok'", "then": "b", "otherwise": "b"}},
            output_schema=_schema({"status": "string"}),
        ),
        _state("b"),
    ]

    report = _report(states, require=True)

    assert {r.consumer_state_id for r in report.records if r.kind is RecordKind.COVERAGE} == {"a", "b"}


def test_ac21_a_state_that_could_not_be_analysed_gets_no_coverage_error():
    class _Hostile(SimpleNamespace):
        @property
        def input_schema(self):
            raise RuntimeError("boom")

    hostile = _Hostile(id="h", assistant_id="x", next=None, output_schema=None, task="", finish_iteration=False)

    report = _report([hostile], require=True)

    assert not [r for r in report.records if r.kind is RecordKind.COVERAGE]
    assert [r.reported_as for r in report.records if r.kind is RecordKind.ANALYSIS_FAILURE] == [ReportedAs.INFO]


# --- AC-22: no new rejection for workflows without the new keys -------------------------------------


def _load_templates():
    for path in sorted(TEMPLATES_DIR.glob("*_template.yaml")):
        template = WorkflowConfigTemplate.from_yaml(path.read_text())
        if template is not None:
            template.parse_execution_config()
            yield path.name, template


@pytest.mark.parametrize("name", ["corpus"])
def test_ac22_built_in_templates_have_no_rejection_in_default_mode(name):
    checked = 0
    for template_name, template in _load_templates():
        result = simulate_workflow(template.states, custom_nodes=template.custom_nodes, tools=template.tools)
        report = build_report(result, states=template.states, settings=ContractSettings())
        assert report.blocking_records() == [], template_name
        assert all(r.reported_as is not None for r in report.records), template_name
        checked += 1
    assert checked > 50


def test_ac22_derived_errors_alone_never_reject_in_default_mode():
    states = [
        _state("a", resolve_dynamic_values_in_prompt=True, task="{{ doc }}", next={"state_id": "b"}),
        _tool_state("b", tool_args={"url": "x"}),
    ]
    tools = [WorkflowTool(id="tool-1", tool="searcher")]

    report = _all_known(states, tools=tools, resolver=_FakeResolver({"searcher": frozenset({"url", "query"})}))

    assert any(r.outcome is Outcome.ERROR for r in report.records)
    assert report.blocking_records() == []


# --- failure isolation ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "states",
    [
        [WorkflowState.model_construct(id="a", assistant_id="x", next=None)],
        [_state("a", output_schema="123")],
        [_state("a", output_schema='"just a string"')],
        [_state("a", {"state_id": "ghost"})],
        [_state("a", {"state_id": "b"}), {"id": "b"}],
        [_state("a"), _state("a")],
    ],
    ids=["null-next", "scalar-output-schema", "string-output-schema", "missing-target", "non-state", "duplicate-id"],
)
@pytest.mark.parametrize("mode", [WARN, STRICT])
def test_malformed_fixtures_give_records_not_exceptions(states, mode):
    report = _report(states, mode=mode)

    assert report.status is ContractAnalysisStatus.ANALYSED
    assert all(r.reported_as is not None for r in report.records)
    assert all(r.message for r in report.records)


@pytest.mark.parametrize(
    ("states", "fragment"),
    [
        ([_state("a", {"state_id": "ghost"})], "does not exist"),
        ([_state("a", {"state_id": "b"}), {"id": "b"}], "not a workflow state"),
    ],
)
def test_could_not_analyse_records_never_reject_in_warn_and_do_in_strict(states, fragment):
    warn_report = _report(states, mode=WARN)
    strict_report = _report(states, mode=STRICT)

    failures = lambda report: [r for r in report.records if r.kind is RecordKind.ANALYSIS_FAILURE]  # noqa: E731
    assert failures(warn_report) and all(r.outcome is Outcome.UNVERIFIED for r in failures(warn_report))
    assert {r.reported_as for r in failures(warn_report)} == {ReportedAs.INFO}
    assert {r.reported_as for r in failures(strict_report)} == {ReportedAs.BLOCKS}
    assert any(fragment in r.message for r in failures(warn_report))


def test_per_state_failure_is_governed_by_that_states_mode():
    class _Hostile(SimpleNamespace):
        @property
        def input_schema(self):
            raise RuntimeError("boom")

    hostile = _Hostile(
        id="h",
        assistant_id="x",
        next=None,
        output_schema=None,
        task="",
        finish_iteration=False,
        save_validation="strict",
    )

    report = _report([hostile])

    assert [r.reported_as for r in report.records if r.kind is RecordKind.ANALYSIS_FAILURE] == [ReportedAs.BLOCKS]


def test_template_with_no_states_gives_an_empty_report():
    report = _report([], require=True)

    assert report.records == ()
    assert report.status is ContractAnalysisStatus.ANALYSED
    assert (report.validated_count, report.reachable_total) == (0, 0)


@pytest.mark.parametrize(("mode", "expected"), [(WARN, ReportedAs.INFO), (STRICT, ReportedAs.BLOCKS)])
def test_simulation_wide_failure_is_one_graph_level_record_governed_by_the_workflow_mode(mode, expected):
    states = [_state("a", save_validation="warn")]

    report = failure_report(ContractSettings(mode=mode), CAUSE_SIMULATION_FAILED, states=states)

    assert len(report.records) == 1
    record = report.records[0]
    assert (record.kind, record.outcome, record.consumer_state_id) == (
        RecordKind.ANALYSIS_FAILURE,
        Outcome.UNVERIFIED,
        "",
    )
    assert record.reported_as is expected
    assert record.label == CAUSE_SIMULATION_FAILED
    assert report.status is ContractAnalysisStatus.FAILED


@pytest.mark.parametrize("workflow_mode", [WARN, STRICT])
def test_simulation_wide_failure_blocks_when_any_state_is_strict(workflow_mode):
    states = [_state("a", save_validation="warn"), _state("b", save_validation="strict")]

    report = failure_report(ContractSettings(mode=workflow_mode), CAUSE_SIMULATION_FAILED, states=states)

    assert [r.reported_as for r in report.records] == [ReportedAs.BLOCKS]


def test_simulation_wide_failure_is_info_when_no_state_and_not_the_workflow_is_strict():
    states = [_state("a", save_validation="warn"), _state("b")]

    report = failure_report(ContractSettings(mode=WARN), CAUSE_SIMULATION_FAILED, states=states)

    assert [r.reported_as for r in report.records] == [ReportedAs.INFO]


def test_a_record_whose_message_cannot_be_rendered_keeps_the_class_it_would_have_had():
    class _Boom:
        @property
        def name(self):
            raise RuntimeError("boom")

    declared = simulate_workflow([_entry("a"), _needs("a", {"doc": "string"})]).findings[0]
    assert declared.record.outcome is Outcome.ERROR and declared.record.kind is RecordKind.DECLARED
    derived = Finding(
        record=declared.record.model_copy(update={"kind": RecordKind.DERIVED}),
        reason=simulate_model.REASON_NOT_GUARANTEED,
        requirement=_Boom(),
    )
    rejecting = Finding(
        record=declared.record.model_copy(), reason=simulate_model.REASON_NOT_GUARANTEED, requirement=_Boom()
    )
    result = SimulationResult(findings=(rejecting, derived), reachable_state_ids=("a",))

    report = build_report(result, states=[_state("a")], settings=ContractSettings(mode=WARN))

    assert [r.reported_as for r in report.records] == [ReportedAs.BLOCKS, ReportedAs.WARNING]
    assert all(r.message == "This result could not be described." for r in report.records)


def test_iteration_item_may_supply_message_names_the_removing_node_and_the_mechanism():
    states = [
        _state(
            "seed",
            {"state_id": "source", "clear_context_store": "keep_current"},
            output_schema=_schema({"extra": "string"}),
        ),
        _state("source", {"state_id": "branch", "iter_key": "items", "include_in_iterator_context": ["none"]}),
        _needs("branch", {"extra": "string"}),
    ]

    report = _report(states)

    record = _row(report, "branch", "input_schema.extra")
    assert (record.outcome, record.reported_as) == (Outcome.UNVERIFIED, ReportedAs.INFO)
    assert "iteration item may supply it" in record.message
    assert "include_in_iterator_context" in record.message


def test_reached_cap_is_one_graph_level_record_tagged_with_its_cause():
    states = [_state("a", {"state_id": "b"}), _state("b", {"state_id": "c"}), _state("c")]
    result = simulate_workflow(states, work_cap=1)

    report = build_report(result, states=states, settings=ContractSettings(mode=STRICT))

    graph_level = [r for r in report.records if r.kind is RecordKind.ANALYSIS_FAILURE and r.consumer_state_id == ""]
    assert report.cap_reached is True
    assert len(graph_level) == 1
    assert graph_level[0].reported_as is ReportedAs.BLOCKS
    assert graph_level[0].label == "work_cap_reached"
    assert "work limit" in graph_level[0].message


def test_policy_failure_on_one_record_degrades_that_record_only():
    class _Boom:
        @property
        def name(self):
            raise RuntimeError("boom")

    good = simulate_workflow([_needs("a", {"doc": "string"})]).findings[0]
    bad = Finding(record=good.record.model_copy(), reason=simulate_model.REASON_NOT_GUARANTEED, requirement=_Boom())
    result = SimulationResult(findings=(bad, good), reachable_state_ids=("a",))

    report = build_report(result, states=[_state("a")], settings=ContractSettings(mode=STRICT))

    # The unreadable record's strict form check also failed: strict reports that as one more blocking record.
    form_check_failures = [r for r in report.records if r.label == policy_module.REASON_FORM_CHECK_FAILED]
    assert len(form_check_failures) == 1
    assert len(report.records) - len(form_check_failures) == 2
    assert all(r.reported_as is not None and r.message for r in report.records)


@pytest.mark.parametrize(("mode", "expected"), [(WARN, ReportedAs.INFO), (STRICT, ReportedAs.BLOCKS)])
def test_a_record_the_policy_leaves_unclassified_fails_the_report_closed(mode, expected):
    """The report refuses an unclassified record, and the policy's own fallback then rejects in strict."""
    states = [_needs("a", {"doc": "string"})]
    result = simulate_workflow(states)

    with patch.object(policy_module, "_reported_as", return_value=None):
        report = build_report(result, states=states, settings=ContractSettings(mode=mode))

    assert report.status is ContractAnalysisStatus.FAILED
    assert [(r.label, r.reported_as) for r in report.records] == [(CAUSE_POLICY_FAILED, expected)]


class _ExplodingStates:
    def __iter__(self):
        raise RuntimeError("boom")


def test_policy_wide_failure_gives_one_graph_level_record():
    report = build_report(SimulationResult(), states=_ExplodingStates(), settings=ContractSettings(mode=STRICT))

    assert [(r.kind, r.reported_as, r.label) for r in report.records] == [
        (RecordKind.ANALYSIS_FAILURE, ReportedAs.BLOCKS, CAUSE_POLICY_FAILED)
    ]


def test_policy_wide_failure_blocks_when_only_one_state_is_strict():
    states = [_state("a", save_validation="warn"), _state("b", save_validation="strict")]
    result = simulate_workflow(states)

    with patch.object(policy_module, "_build_report", side_effect=RuntimeError("boom")):
        report = build_report(result, states=states, settings=ContractSettings(mode=WARN))

    assert [(r.label, r.reported_as) for r in report.records] == [(CAUSE_POLICY_FAILED, ReportedAs.BLOCKS)]


def test_a_failing_form_check_adds_nothing_when_nothing_is_strict():
    states = [
        _state("a", {"state_id": "b"}, output_schema=EXAMPLE_DOC),
        _needs("b", {"doc": "string"}),
    ]
    result = simulate_workflow(states, unknown_initial_input=False)

    with patch.object(policy_module, "_strict_candidates", side_effect=RuntimeError("boom")):
        report = build_report(result, states=states, settings=ContractSettings(mode=WARN))

    assert not [r for r in report.records if r.label == policy_module.REASON_FORM_CHECK_FAILED]


def test_a_failing_form_check_blocks_a_state_level_strict_consumer_in_a_warn_workflow():
    states = [
        _state("a", {"state_id": "b"}, output_schema=EXAMPLE_DOC),
        _needs("b", {"doc": "string"}, save_validation="strict"),
    ]
    result = simulate_workflow(states, unknown_initial_input=False)

    with patch.object(policy_module, "_strict_candidates", side_effect=RuntimeError("boom")):
        report = build_report(result, states=states, settings=ContractSettings(mode=WARN))

    failures = [r for r in report.records if r.label == policy_module.REASON_FORM_CHECK_FAILED]
    assert [(r.consumer_state_id, r.reported_as) for r in failures] == [("b", ReportedAs.BLOCKS)]


def test_a_failing_strict_form_check_still_blocks_the_consumer_as_unverified():
    states = [
        _state("a", {"state_id": "b"}, output_schema=EXAMPLE_DOC),
        _needs("b", {"doc": "string"}),
    ]
    result = simulate_workflow(states, unknown_initial_input=False)

    with patch.object(policy_module, "_strict_candidates", side_effect=RuntimeError("boom")):
        report = build_report(result, states=states, settings=ContractSettings(mode=STRICT))

    failures = [
        r
        for r in report.records
        if r.kind is RecordKind.ANALYSIS_FAILURE and r.label == policy_module.REASON_FORM_CHECK_FAILED
    ]
    assert [(r.consumer_state_id, r.outcome, r.reported_as) for r in failures] == [
        ("b", Outcome.UNVERIFIED, ReportedAs.BLOCKS)
    ]
    assert "'b'" in failures[0].message


# --- the bare field name of a record (EPMCDME-16504, Task 2) -------------------------------------------


def test_a_declared_input_record_carries_the_bare_field_name():
    report = _report([_entry("a"), _needs("a", {"doc": "string"})])

    record = _row(report, "a", "input_schema.doc")
    assert (record.kind, record.field) == (RecordKind.DECLARED, "doc")


def test_a_tool_binding_record_carries_the_bare_parameter_name():
    tools = [WorkflowTool(id="tool-1", tool="searcher")]

    report = _report([_tool_state("a", tool_args={"url": None})], tools=tools)

    record = _row(report, "a", "tool_args.url")
    assert (record.kind, record.field) == (RecordKind.DERIVED, "url")


def test_a_missing_mandatory_parameter_record_carries_the_bare_parameter_name():
    tools = [WorkflowTool(id="tool-1", tool="searcher")]
    resolver = _FakeResolver({"searcher": frozenset({"url", "query"})})

    report = _report([_tool_state("a", tool_args={"url": "literal"})], tools=tools, resolver=resolver)

    assert _row(report, "a", "tool_args.query").field == "query"


def test_an_output_schema_note_record_carries_the_bare_field_name():
    from codemie.core.workflow_models.workflow_models import CustomWorkflowNode

    node = CustomWorkflowNode(
        id="t1",
        custom_node_id="transform_node",
        config={"mappings": [{"type": "constant", "value": "x", "output_field": "a"}]},
    )
    states = [WorkflowState(id="a", custom_node_id="t1", next={}, output_schema=_schema({"b": "string"}))]
    result = simulate_workflow(states, custom_nodes=[node])

    report = build_report(result, states=states, settings=ContractSettings(mode=WARN))

    record = _row(report, "a", "output_schema.b")
    assert (record.kind, record.field) == (RecordKind.CONTRACT_FORM, "b")


def test_a_state_strict_consumer_form_record_carries_the_context_field_name():
    states = [
        _state("a", {"state_id": "b"}, output_schema=EXAMPLE_DOC),
        _needs("b", {"doc": "string"}, save_validation="strict"),
    ]

    report = _report(states)

    [record] = _form_errors(report)
    assert (record.consumer_state_id, record.path, record.field) == ("b", "input_schema.doc", "doc")


def test_a_workflow_strict_producer_form_record_has_no_field():
    states = [
        _state("a", {"state_id": "b"}, output_schema=EXAMPLE_DOC),
        _needs("b", {"doc": "string"}),
    ]

    report = _report(states, mode=STRICT)

    [record] = _form_errors(report)
    assert (record.consumer_state_id, record.path, record.field) == ("a", "output_schema", None)


def test_a_declared_non_schema_producer_form_record_that_feeds_nobody_has_no_field():
    states = [_state("a", {"state_id": "b"}, output_schema=EXAMPLE_DOC), _state("b")]

    report = _report(states, mode=STRICT)

    assert [r.field for r in _form_errors(report)] == [None]


def test_graph_level_and_coverage_records_have_no_field():
    states = [_state("a", {"state_id": "b"}), _state("b"), _state("c", {"state_id": "d"})]
    capped = build_report(simulate_workflow(states, work_cap=1), states=states, settings=ContractSettings(mode=STRICT))
    covered = _report([_needs("a", {"seed": "string"}, {"state_id": "b"}), _state("b")], require=True)

    graph_level = [r for r in capped.records if r.kind is RecordKind.ANALYSIS_FAILURE and r.consumer_state_id == ""]
    coverage = [r for r in covered.records if r.kind is RecordKind.COVERAGE]
    assert graph_level and coverage
    assert [r.field for r in graph_level + coverage] == [None] * (len(graph_level) + len(coverage))


def test_a_record_whose_message_cannot_be_rendered_keeps_its_field():
    class _Boom:
        @property
        def name(self):
            raise RuntimeError("boom")

    declared = simulate_workflow([_entry("a"), _needs("a", {"doc": "string"})]).findings[0]
    assert declared.record.field == "doc"
    broken = Finding(
        record=declared.record.model_copy(), reason=simulate_model.REASON_NOT_GUARANTEED, requirement=_Boom()
    )
    result = SimulationResult(findings=(broken,), reachable_state_ids=("a",))

    report = build_report(result, states=[_state("a")], settings=ContractSettings(mode=WARN))

    [record] = report.records
    assert record.message == "This result could not be described."
    assert record.field == "doc"


def _report_for_field(field: str):
    declared = simulate_workflow([_entry("a"), _needs("a", {"doc": "string"})]).findings[0]
    hostile = declared.record.model_copy(update={"field": field, "path": f"input_schema.{field}"})
    finding = Finding(record=hostile, reason=declared.reason, requirement=declared.requirement)
    result = SimulationResult(findings=(finding,), reachable_state_ids=("a",))
    return build_report(result, states=[_state("a")], settings=ContractSettings(mode=WARN))


def test_the_json_fields_keep_the_raw_names_while_the_message_stays_sanitised():
    [record] = _report_for_field("<b>doc</b>").records

    assert record.field == "<b>doc</b>"
    assert record.path == "input_schema.<b>doc</b>"
    assert "<" not in record.message


def test_distinct_non_ascii_and_spaced_names_stay_distinct_in_the_json_fields():
    names = ["имя", "дом", "user name"]

    records = [_report_for_field(name).records[0] for name in names]

    assert [r.field for r in records] == names
    assert len({r.path for r in records}) == len(names)


def test_the_json_fields_lose_control_characters_and_are_length_capped():
    [record] = _report_for_field("do\x00c\n\u2028x\x7f" + "y" * 1000).records

    assert record.field is not None
    assert not any(ord(c) < 32 or c in "\x7f\u2028" for c in record.field + record.path)
    assert record.field.startswith("docx" + "y")
    assert len(record.field) <= 200
    assert len(record.path) <= 200


# --- messages ---------------------------------------------------------------------------------------


def _reason_codes() -> set[str]:
    codes = {v for k, v in vars(simulate_model).items() if k.startswith("REASON_") and isinstance(v, str)}
    codes |= {v for k, v in vars(derive).items() if k.startswith("REASON_") and isinstance(v, str)}
    codes |= {effects_module.NOTE_TYPE_CONFLICT, effects_module.NOTE_DECLARED_FIELD_NOT_PRODUCED}
    codes.add(effects_module.NOTE_OUTPUT_CONTRACT_IGNORED)
    return codes


def test_every_reason_code_has_a_fixed_template():
    assert _reason_codes() <= set(MESSAGE_TEMPLATES)


def test_no_message_is_a_bare_reason_code_and_none_carries_markup():
    states = [
        _state("a", {"state_id": "b"}, output_schema=EXAMPLE_DOC),
        _needs("b", {"doc": "string", "other": "string"}, {"state_id": "c", "reset_keys_in_context_store": ["doc"]}),
        _state("c", resolve_dynamic_values_in_prompt=True, task="{{ doc }} {{ nope }}"),
    ]

    report = _all_known(states, mode=STRICT)

    codes = _reason_codes()
    assert report.records
    for record in report.records:
        assert record.message not in codes
        assert not set("<>&") & set(record.message + record.path)


@pytest.mark.parametrize(
    ("states", "path", "fragments"),
    [
        ([_entry("a"), _needs("a", {"doc": "string"})], "input_schema.doc", ["'doc'", "not guaranteed"]),
        (
            [
                _state("a", {"state_id": "b", "clear_context_store": "keep_current"}, output_schema=DOC_SCHEMA),
                _state("b", {"state_id": "c", "reset_keys_in_context_store": ["doc"]}),
                _needs("c", {"doc": "string"}),
            ],
            "input_schema.doc",
            ["'doc'", "removed", "'b'", "reset_keys_in_context_store"],
        ),
    ],
)
def test_templates_name_the_field_the_node_and_the_mechanism(states, path, fragments):
    report = _all_known(states)

    state_id = states[-1].id
    message = _row(report, state_id, path).message
    for fragment in fragments:
        assert fragment in message


def test_names_are_sanitised_in_messages_and_provenance_but_kept_raw_in_path_and_field():
    hostile = "<img src=x onerror=1>"
    states = [
        _state("a", {"state_id": "b"}, output_schema=DOC_SCHEMA),
        _tool_state("b", tool_args={hostile: None}),
    ]

    report = _all_known(states, tools=[WorkflowTool(id="tool-1", tool="g")])

    record = next(r for r in report.records if r.path.startswith("tool_args."))
    text = "".join(
        [record.message, record.label or "", *record.provenance.producers, *record.provenance.sources_checked]
    )
    assert "<" not in text and ">" not in text and "&" not in text
    assert "img" in record.message
    assert record.location.segments == ("tool_args", hostile)
    assert record.path == f"tool_args.{hostile}"


def test_names_are_length_capped():
    long_name = "x" * 500
    report = _all_known([_needs("a", {long_name: "string"})])

    record = report.records[0]
    assert long_name not in record.message
    assert len(record.message) < 400


def test_safe_name_replaces_unsafe_characters_and_caps_length():
    assert safe_name("<img src=x onerror=1>") == "_img_src_x_onerror_1_"
    assert safe_name("a&b") == "a_b"
    assert len(safe_name("y" * 1000)) <= 40
    assert safe_name(None) == "None"


def test_ac15_message_omits_the_binding_value():
    secret = "https://secret.example.test/token=abc"
    states = [
        _state("a", {"state_id": "b"}, output_schema=_schema({"url": "string"})),
        _tool_state("b", tool_args={"url": secret}),
    ]

    report = _report(states, tools=[WorkflowTool(id="tool-1", tool="generic")])

    record = _row(report, "b", "tool_args.url")
    assert record.outcome is Outcome.WARNING
    assert "secret.example" not in report.to_json()
    assert "context" in record.message


def test_example_form_text_is_never_echoed():
    states = [
        _state("a", {"state_id": "b"}, output_schema='{"doc": "TOP-SECRET-EXAMPLE-TEXT"}'),
        _needs("b", {"doc": "string"}),
    ]

    report = _report(states, mode=STRICT)

    assert "TOP-SECRET-EXAMPLE-TEXT" not in report.to_json()


# --- report data ------------------------------------------------------------------------------------


def test_report_carries_the_coverage_data_of_the_simulation():
    states = [_needs("a", {"seed": "string"}, {"state_id": "b"}), _state("b"), _state("orphan")]

    report = _report(states)

    assert report.status is ContractAnalysisStatus.ANALYSED
    assert report.uncovered_state_ids == ("b",)
    assert report.unreachable_state_ids == ("orphan",)
    assert (report.validated_count, report.reachable_total) == (1, 2)
    assert report.cap_reached is False


# --- strict_source: which level made a record block because of strict ---------------------------------------------


def _derived_placeholder_states(**overrides):
    return [_state("a", resolve_dynamic_values_in_prompt=True, task="{{ doc }}", **overrides)]


def test_strict_source_is_workflow_for_a_record_that_blocks_only_because_the_workflow_is_strict():
    report = _all_known(_derived_placeholder_states(), mode=STRICT)

    record = _row(report, "a", "task")
    assert (record.reported_as, record.strict_source) == (ReportedAs.BLOCKS, StrictSource.WORKFLOW)


def test_strict_source_is_state_for_a_record_that_blocks_only_because_the_state_is_strict():
    report = _all_known(_derived_placeholder_states(save_validation="strict"), mode=WARN)

    record = _row(report, "a", "task")
    assert (record.reported_as, record.strict_source) == (ReportedAs.BLOCKS, StrictSource.STATE)


def test_strict_source_is_not_set_for_a_record_that_does_not_block_in_warn():
    report = _all_known(_derived_placeholder_states(), mode=WARN)

    record = _row(report, "a", "task")
    assert (record.reported_as, record.strict_source) == (ReportedAs.WARNING, None)


def test_strict_source_is_not_set_for_a_state_that_is_warn_inside_a_strict_workflow():
    report = _all_known(_derived_placeholder_states(save_validation="warn"), mode=STRICT)

    record = _row(report, "a", "task")
    assert (record.reported_as, record.strict_source) == (ReportedAs.WARNING, None)


@pytest.mark.parametrize("mode", [WARN, STRICT])
def test_strict_source_is_not_set_for_a_declared_error_that_blocks_in_any_mode(mode):
    report = _all_known([_entry("a"), _needs("a", {"doc": "string"})], mode=mode)

    record = _row(report, "a", "input_schema.doc")
    assert (record.reported_as, record.strict_source) == (ReportedAs.BLOCKS, None)


def test_strict_source_is_not_set_for_a_record_that_is_info_in_strict():
    states = [_state("a", {"state_id": "b"}, output_schema=DOC_SCHEMA), _needs("b", {"doc": "string"})]

    report = _all_known(states, mode=STRICT)

    ok = [r for r in report.records if r.outcome is Outcome.OK]
    assert ok
    assert [(r.reported_as, r.strict_source) for r in ok] == [(ReportedAs.INFO, None)] * len(ok)


def test_strict_source_is_workflow_on_a_strict_contract_form_finding_of_a_strict_workflow():
    states = [_state("a", {"state_id": "b"}, output_schema=EXAMPLE_DOC), _needs("b", {"doc": "string"})]

    report = _report(states, mode=STRICT)

    assert [r.strict_source for r in _form_errors(report)] == [StrictSource.WORKFLOW]


def test_strict_source_is_state_on_a_strict_contract_form_finding_of_a_state_override():
    states = [
        _state("a", {"state_id": "b"}, output_schema=EXAMPLE_DOC),
        _needs("b", {"doc": "string"}, save_validation="strict"),
    ]

    report = _report(states, mode=WARN)

    assert [r.strict_source for r in _form_errors(report)] == [StrictSource.STATE]


@pytest.mark.parametrize(("mode", "expected"), [(WARN, StrictSource.STATE), (STRICT, StrictSource.WORKFLOW)])
def test_strict_source_is_on_the_fail_closed_form_check_record(mode, expected):
    states = [
        _state("a", {"state_id": "b"}, output_schema=EXAMPLE_DOC),
        _needs("b", {"doc": "string"}, save_validation="strict"),
    ]
    result = simulate_workflow(states, unknown_initial_input=False)

    with patch.object(policy_module, "_strict_candidates", side_effect=RuntimeError("boom")):
        report = build_report(result, states=states, settings=ContractSettings(mode=mode))

    failures = [r for r in report.records if r.label == policy_module.REASON_FORM_CHECK_FAILED]
    assert [(r.reported_as, r.strict_source) for r in failures] == [(ReportedAs.BLOCKS, expected)]


@pytest.mark.parametrize("mode", [WARN, STRICT])
def test_strict_source_is_never_set_on_a_coverage_finding(mode):
    states = [_needs("a", {"seed": "string"}, {"state_id": "b"}), _state("b")]

    report = _report(states, mode=mode, require=True)

    coverage = [r for r in report.records if r.kind is RecordKind.COVERAGE]
    assert [(r.reported_as, r.strict_source) for r in coverage] == [(ReportedAs.BLOCKS, None)]


def test_strict_source_is_never_set_on_a_coverage_finding_of_a_strict_state():
    report = _report([_state("a", save_validation="strict")], require=True)

    coverage = [r for r in report.records if r.kind is RecordKind.COVERAGE]
    assert [(r.reported_as, r.strict_source) for r in coverage] == [(ReportedAs.BLOCKS, None)]


@pytest.mark.parametrize(
    ("workflow_mode", "states", "expected"),
    [
        (STRICT, [_state("a")], StrictSource.WORKFLOW),
        (STRICT, [_state("a", save_validation="strict")], StrictSource.WORKFLOW),
        (WARN, [_state("a", save_validation="warn"), _state("b", save_validation="strict")], StrictSource.STATE),
    ],
    ids=["workflow", "workflow-and-state", "state-only"],
)
def test_strict_source_is_set_on_a_graph_level_failure_that_strict_blocked(workflow_mode, states, expected):
    report = failure_report(ContractSettings(mode=workflow_mode), CAUSE_SIMULATION_FAILED, states=states)

    assert [(r.reported_as, r.strict_source) for r in report.records] == [(ReportedAs.BLOCKS, expected)]


def test_strict_source_is_not_set_on_a_graph_level_failure_that_does_not_block():
    states = [_state("a", save_validation="warn"), _state("b")]

    report = failure_report(ContractSettings(mode=WARN), CAUSE_SIMULATION_FAILED, states=states)

    assert [(r.reported_as, r.strict_source) for r in report.records] == [(ReportedAs.INFO, None)]


def test_strict_source_is_set_on_a_policy_wide_failure_that_strict_blocked():
    report = build_report(SimulationResult(), states=_ExplodingStates(), settings=ContractSettings(mode=STRICT))

    assert [(r.label, r.strict_source) for r in report.records] == [(CAUSE_POLICY_FAILED, StrictSource.WORKFLOW)]


def test_strict_source_is_not_set_when_the_settings_could_not_be_read():
    # The linker assumes strict here (fail closed); the author did not set it.
    report = failure_report(ContractSettings(mode=STRICT), CAUSE_SETTINGS_FAILED, states=[_state("a")])

    assert [(r.label, r.reported_as, r.strict_source) for r in report.records] == [
        (CAUSE_SETTINGS_FAILED, ReportedAs.BLOCKS, None)
    ]


def test_strict_source_survives_a_record_that_cannot_be_rendered():
    states = _derived_placeholder_states()
    result = simulate_workflow(states, unknown_initial_input=False)

    with patch.object(policy_module, "render_message", side_effect=RuntimeError("boom")):
        report = build_report(result, states=states, settings=ContractSettings(mode=STRICT))

    record = _row(report, "a", "task")
    assert (record.reported_as, record.strict_source) == (ReportedAs.BLOCKS, StrictSource.WORKFLOW)


def test_json_names_remove_all_control_ranges_and_keep_unicode_and_markup_characters():
    controls = ''.join(chr(code) for code in [*range(32), *range(127, 160), 0x2028, 0x2029])
    name = "ключ<>&"
    record = _report_for_field(controls + name).records[0]
    assert record.field == name
    assert record.path == f"input_schema.{name}"
