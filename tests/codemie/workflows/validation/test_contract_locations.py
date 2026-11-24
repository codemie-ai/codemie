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

"""Source addresses from real derivation through the linker report (EPMCDME-16503-C)."""

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from codemie.core.workflow_models.workflow_models import CustomWorkflowNode, WorkflowState, WorkflowTool
from codemie.workflows.contracts import policy
from codemie.workflows.contracts.linker import link_workflow_contracts
from codemie.workflows.contracts.models import (
    ContractAnalysisStatus,
    ContractLinkReport,
    ContractLocation,
    LocationSection,
    RecordKind,
    ReportedAs,
)

EMPTY_SCHEMA = '{"type":"object","properties":{}}'


class _Resolver:
    def __init__(self, names):
        self.names = names

    def mandatory_params(self, tool):
        return self.names


def _state(state_id="consumer", **kwargs):
    values = {"id": state_id, "assistant_id": "assistant", "next": {}, "output_schema": EMPTY_SCHEMA}
    values.update(kwargs)
    return WorkflowState(**values)


def _report(state, *, tools=(), custom_nodes=(), mandatory=frozenset(), mode="warn", require=False, extra=()):
    entry = _state("entry", input_schema=EMPTY_SCHEMA, next={"state_id": state.id})
    config = SimpleNamespace(
        states=[entry, state, *extra],
        tools=list(tools),
        custom_nodes=list(custom_nodes),
        yaml_config=f"save_validation: {mode}\nrequire_input_contracts: {str(require).lower()}",
    )
    report = link_workflow_contracts(config, _Resolver(mandatory))
    assert report.status is ContractAnalysisStatus.ANALYSED
    return report


def _rows(report, state_id="consumer", kind=RecordKind.DERIVED):
    return [r for r in report.records if r.consumer_state_id == state_id and r.kind is kind]


def _location(record, section, owner, *segments):
    assert record.location == ContractLocation(section=section, owner_id=owner, segments=segments)
    assert record.consumer_state_id == "consumer"


@pytest.mark.parametrize("bindings", [None, {}], ids=["absent", "empty"])
def test_tool_bindings_are_used_when_state_bindings_are_empty(bindings):
    tool = WorkflowTool(id="tool", tool="search", tool_args={"query": "{{ missing }}", "literal": "fixed"})
    state = _state(assistant_id=None, tool_id="tool", tool_args=bindings)
    rows = _rows(_report(state, tools=[tool]))
    assert len(rows) == 3  # Same-name override plus template reference; the literal has only its override.
    for row in rows:
        _location(row, "tools", "tool", "tool_args", "literal" if row.path.endswith("literal") else "query")


def test_nonempty_state_bindings_replace_tool_bindings_and_select_the_state_owner():
    tool = WorkflowTool(id="tool", tool="search", tool_args={"ignored": "{{ not_read }}"})
    state = _state(assistant_id=None, tool_id="tool", tool_args={"literal": "fixed", "query": "{{ missing }}"})
    rows = _rows(_report(state, tools=[tool]))
    assert len(rows) == 3
    assert not any("ignored" in row.path for row in rows)
    for row in rows:
        _location(row, "states", "consumer", "tool_args", "literal" if row.path.endswith("literal") else "query")


@pytest.mark.parametrize(
    "state_args,tool_args,section,segments",
    [
        (None, None, "tools", ()),
        ({}, {}, "tools", ()),
        ({"other": "fixed"}, {"ignored": None}, "states", ("tool_args",)),
        (None, {"other": "fixed"}, "tools", ("tool_args",)),
    ],
)
def test_implicit_or_unbound_mandatory_parameters_use_the_selected_parent(state_args, tool_args, section, segments):
    state = _state(assistant_id=None, tool_id="tool", tool_args=state_args)
    tool = WorkflowTool(id="tool", tool="search", tool_args=tool_args)
    rows = _rows(_report(state, tools=[tool], mandatory=frozenset({"needed"})))
    row = next(row for row in rows if row.path == "tool_args.needed")
    _location(row, section, "consumer" if section == "states" else "tool", *segments)


def test_unknown_tool_schema_points_at_the_tool_and_unresolved_tool_points_at_its_reference():
    state = _state(assistant_id=None, tool_id="tool")
    row = _rows(_report(state, tools=[WorkflowTool(id="tool", tool="search")], mandatory=None))[0]
    _location(row, "tools", "tool")
    row = _rows(_report(state, mandatory=None))[0]
    _location(row, "states", "consumer", "tool_id")


@pytest.mark.parametrize("key", ["1", "01", "a.b[1]", "<x>&\n", "ключ", "long-" * 80])
@pytest.mark.parametrize("mode,reported_as", [("warn", ReportedAs.WARNING), ("strict", ReportedAs.BLOCKS)])
def test_exact_binding_keys_survive_policy_serialization_and_copy(key, mode, reported_as):
    tool = WorkflowTool(id="tool", tool="search", tool_args={key: "{{ absent }}"})
    report = _report(_state(assistant_id=None, tool_id="tool"), tools=[tool], mode=mode)
    row = next(row for row in _rows(report) if row.reported_as is reported_as)
    _location(row, "tools", "tool", "tool_args", key)
    assert type(row.location.segments[-1]) is str
    assert row.model_copy(deep=True).location == row.location
    copied = report.model_copy(deep=True)
    assert copied.records == report.records
    restored = ContractLinkReport.model_validate_json(report.to_json())
    assert restored == report
    encoded = next(
        r
        for r in report.to_dict()["records"]
        if r["consumer_state_id"] == "consumer" and r["reported_as"] == reported_as
    )
    assert encoded["location"] == {"section": "tools", "owner_id": "tool", "segments": ["tool_args", key]}
    with pytest.raises(ValidationError):
        row.location.owner_id = "changed"


def test_agent_task_and_declared_input_schema_have_state_locations():
    state = _state(
        task="{{ task_input }}",
        resolve_dynamic_values_in_prompt=True,
        input_schema='{"type":"object","properties":{"declared":{"type":"string"}},"required":["declared"]}',
    )
    report = _report(state)
    _location(_rows(report)[0], "states", "consumer", "task")
    declared = _rows(report, kind=RecordKind.DECLARED)[0]
    assert declared.path == "input_schema.declared"
    _location(declared, "states", "consumer", "input_schema", "properties", "declared")


@pytest.mark.parametrize(
    "input_key,segments", [(None, ("config", "mappings", 1, "source_path")), ("payload.items", ("config", "input_key"))]
)
def test_transform_inputs_have_custom_node_locations_and_integer_mapping_indices(input_key, segments):
    config = {
        "mappings": [
            {"type": "constant", "output_field": "constant", "value": 1},
            {"type": "extract", "source_path": "external.value", "output_field": "result"},
        ]
    }
    if input_key:
        config["input_key"] = input_key
    custom = CustomWorkflowNode(id="transform", custom_node_id="transform_node", config=config)
    rows = _rows(_report(_state(assistant_id=None, custom_node_id="transform"), custom_nodes=[custom]))
    assert len(rows) == 1
    _location(rows[0], "custom_nodes", "transform", *segments)
    if not input_key:
        assert type(rows[0].location.segments[2]) is int
        assert rows[0].location.segments[2] != "1"


@pytest.mark.parametrize(
    "transition,segments,path",
    [
        (
            {"condition": {"expression": "missing == 1", "then": "end", "otherwise": "end"}},
            ("next", "condition", "expression"),
            "next.condition.expression",
        ),
        (
            {
                "switch": {
                    "cases": [
                        {"condition": "1 == 1", "state_id": "end"},
                        {"condition": "missing == 1", "state_id": "end"},
                    ],
                    "default": "end",
                }
            },
            ("next", "switch", "cases", 1, "condition"),
            "next.switch.cases[1].condition",
        ),
        ({"iter_key": "missing", "state_id": "end"}, ("next", "iter_key"), "next.iter_key"),
    ],
)
def test_transition_requirements_keep_state_owner_and_exact_tokens(transition, segments, path):
    report = _report(_state(next=transition), extra=[_state("end")])
    row = next(r for r in _rows(report) if r.path == path)
    _location(row, "states", "consumer", *segments)


def test_output_contract_coverage_and_convergence_findings_have_source_locations():
    report = _report(_state(output_schema='{"doc":"example"}'), mode="strict", require=True)
    output = _rows(report, kind=RecordKind.CONTRACT_FORM)[0]
    _location(output, "states", "consumer", "output_schema")
    coverage = _rows(report, kind=RecordKind.COVERAGE)[0]
    _location(coverage, "states", "consumer", "input_schema")

    schema = '{"type":"object","properties":{"shared":{"type":"string"}},"required":["shared"]}'
    fork = _state("fork", next={"state_ids": ["left", "right"]})
    branches = [_state(name, next={"state_id": "consumer"}, output_schema=schema) for name in ("left", "right")]
    report = _report(fork, extra=[*branches, _state()])
    convergence = _rows(report, kind=RecordKind.CONVERGENCE)[0]
    assert convergence.path == "context_store.shared"
    _location(convergence, "states", "consumer")


def test_transform_schema_note_uses_custom_owner_without_changing_diagnostic_path():
    custom = CustomWorkflowNode(
        id="transform",
        custom_node_id="transform_node",
        config={
            "mappings": [{"type": "constant", "output_field": "actual", "value": "text"}],
            "output_schema": {"properties": {"01": {"type": "string"}}},
        },
    )
    report = _report(_state(assistant_id=None, custom_node_id="transform"), custom_nodes=[custom])
    row = next(row for row in _rows(report, kind=RecordKind.CONTRACT_FORM) if row.path == "output_schema.01")
    _location(row, "custom_nodes", "transform", "config", "output_schema", "properties", "01")


def test_strict_consumer_form_finding_keeps_requirement_location_and_consumer():
    producer = _state("producer", output_schema='{"value":"example"}', next={"state_id": "consumer"})
    consumer = _state(save_validation="strict", assistant_id=None, tool_id="tool")
    tool = WorkflowTool(id="tool", tool="search", tool_args={"query": "{{ value }}"})
    report = _report(producer, extra=[consumer], tools=[tool])
    row = _rows(report, kind=RecordKind.CONTRACT_FORM)[0]
    assert row.path == "tool_args.query"
    _location(row, "tools", "tool", "tool_args", "query")


@pytest.mark.parametrize(
    "next_,segments",
    [
        ({"state_id": "missing"}, ("next", "state_id")),
        ({"state_ids": ["end", "missing"]}, ("next", "state_ids", 1)),
        ({"condition": {"expression": "True", "then": "missing", "otherwise": "end"}}, ("next", "condition", "then")),
        (
            {"switch": {"cases": [{"condition": "True", "state_id": "missing"}], "default": "end"}},
            ("next", "switch", "cases", 0, "state_id"),
        ),
    ],
)
def test_dangling_transitions_have_locations_created_with_the_edge(next_, segments):
    report = _report(_state(next=next_), extra=[_state("end")])
    row = _rows(report, kind=RecordKind.ANALYSIS_FAILURE)[0]
    _location(row, "states", "consumer", *segments)


def test_graph_failure_has_explicit_root_location(monkeypatch):
    def fail(_config, _document=None):
        raise ValueError("settings failure")

    monkeypatch.setattr("codemie.workflows.contracts.linker.read_contract_settings", fail)
    report = link_workflow_contracts(SimpleNamespace(states=[_state()]))
    assert report.status is ContractAnalysisStatus.FAILED
    assert report.records[0].location == ContractLocation.workflow()
    assert report.records[0].consumer_state_id == ""


def test_render_fallback_keeps_exact_location(monkeypatch):
    def fail(_finding):
        raise ValueError("render failure")

    monkeypatch.setattr(policy, "render_message", fail)
    key = "01.<key>"
    report = _report(_state(assistant_id=None, tool_id="tool", tool_args={key: "{{ missing }}"}), mode="strict")
    for row in _rows(report):
        if row.path != "tool_args":
            _location(row, "states", "consumer", "tool_args", key)


def test_unknown_location_is_explicit_and_location_model_rejects_ambiguous_types():
    from codemie.workflows.contracts.models import ContractRecord, Outcome

    row = ContractRecord(kind=RecordKind.DERIVED, outcome=Outcome.OK, consumer_state_id="consumer", path="legacy")
    assert row.location is None
    assert row.model_dump()["location"] is None
    assert ContractLocation.state("state", "1") != ContractLocation.state("state", 1)
    assert ContractLocation.state("state", "01") != ContractLocation.state("state", "1")
    with pytest.raises(ValidationError):
        ContractLocation(section=LocationSection.STATES)
    with pytest.raises(ValidationError):
        ContractLocation(section=LocationSection.WORKFLOW, owner_id="owner")
    with pytest.raises(ValidationError):
        ContractLocation.state("state", True)


@pytest.mark.parametrize("state_bindings", [None, {}, {"1": "{{ absent }}"}])
def test_real_linker_locations_reach_issue_lines_for_state_or_tool_bindings(state_bindings):
    import yaml
    from codemie.workflows.validation.contract_issues import build_contract_issues, contract_line_finder

    keys = ["1", "01", "a.b[1]", "ключ", "<img onerror=alert(1)>&"]
    tool = WorkflowTool(id="tool", tool="search", tool_args=dict.fromkeys(keys, "{{ absent }}"))
    state = _state(assistant_id=None, tool_id="tool", tool_args=state_bindings)
    report = _report(state, tools=[tool])
    source = yaml.safe_dump(
        {"tools": [tool.model_dump(exclude_none=True)], "states": [state.model_dump(exclude_none=True)]},
        sort_keys=False,
        allow_unicode=True,
    )
    issues = build_contract_issues(report, contract_line_finder(SimpleNamespace(yaml_config=source)))
    expected = ["1"] if state_bindings else keys
    for key in expected:
        records = [
            record for record in report.records if record.location and record.location.segments == ("tool_args", key)
        ]
        assert records
        entries = [entry for entry in issues.warnings if entry["config_path"] == f"tool_args.{key}"]
        assert entries
        for entry in entries:
            assert entry["config_section"] == ("states" if state_bindings else "tools")
            assert entry["state_id"] == "consumer"
            assert key in source.splitlines()[entry["config_line"] - 1]
    lines = {entry["config_path"]: entry["config_line"] for entry in issues.warnings}
    assert len(set(lines.values())) == len(expected)


def test_typed_lookup_distinguishes_literal_punctuation_and_indices_without_sibling_fallback():
    from codemie.workflows.validation.line_lookup import ValidationSourceContext

    text = '''states:
- id: consumer
  tool_args:
    "1": numeric key
    "01": leading zero
    "a.b[1]": literal punctuation
    a:
      b:
      - first
      - second
'''
    finder = ValidationSourceContext().source(text).finder
    assert finder.find_contract_line(ContractLocation.state("consumer", "tool_args", "1")) == 4
    assert finder.find_contract_line(ContractLocation.state("consumer", "tool_args", "01")) == 5
    assert finder.find_contract_line(ContractLocation.state("consumer", "tool_args", "a.b[1]")) == 6
    assert finder.find_contract_line(ContractLocation.state("consumer", "tool_args", "a", "b", 1)) == 10
    assert (
        finder.find_contract_line(ContractLocation.state("consumer", "tool_args", "a", "b", "1")) == 9
    )  # parent sequence only
    assert finder.find_contract_line(ContractLocation.state("consumer", "missing", "field")) is None
    assert finder.find_contract_line(ContractLocation.state("absent", "tool_args", "1")) is None


def test_custom_mapping_and_transition_locations_survive_real_report_formatting():
    import yaml
    from codemie.workflows.validation.contract_issues import build_contract_issues, contract_line_finder

    custom = CustomWorkflowNode(
        id="transform",
        custom_node_id="transform_node",
        config={
            "mappings": [
                {"type": "constant", "output_field": "constant", "value": 1},
                {"type": "extract", "source_path": "external.value", "output_field": "result"},
            ]
        },
    )
    state = _state(
        assistant_id=None,
        custom_node_id="transform",
        next={
            "switch": {
                "cases": [{"condition": "1 == 1", "state_id": "end"}, {"condition": "absent == 1", "state_id": "end"}],
                "default": "end",
            }
        },
    )
    report = _report(state, custom_nodes=[custom], extra=[_state("end")])
    source = "# Client text\n\n" + yaml.safe_dump(
        {"custom_nodes": [custom.model_dump(exclude_none=True)], "states": [state.model_dump(exclude_none=True)]},
        sort_keys=False,
    )
    issues = build_contract_issues(report, contract_line_finder(SimpleNamespace(yaml_config=source)))
    mapping = next(entry for entry in issues.warnings if entry.get("config_path") == "config.mappings.1.source_path")
    transition = next(entry for entry in issues.warnings if entry.get("config_path") == "next.switch.cases.1.condition")
    assert mapping["config_section"] == "custom_nodes"
    assert transition["config_section"] == "states"
    assert "source_path: external.value" in source.splitlines()[mapping["config_line"] - 1]
    assert "condition: absent == 1" in source.splitlines()[transition["config_line"] - 1]
