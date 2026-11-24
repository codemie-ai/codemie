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

"""Contract issue shaping (EPMCDME-16504, T3): config_path table, selection, outcome mapping, cap and coverage."""

import codemie.workflows.contracts.linker  # noqa: F401  (import order: linker first, then the validation package)
import codemie.workflows.validation  # noqa: F401

from types import SimpleNamespace
import json
import re
from unittest.mock import patch

import pytest
import yaml

from codemie.workflows.constants import ContractValidationMode
from codemie.workflows.contracts.models import (
    ContractAnalysisStatus,
    ContractLinkReport,
    ContractRecord,
    ContractLocation,
    LocationSection,
    Outcome,
    Provenance,
    RecordKind,
    ReportedAs,
    StrictSource,
)
from codemie.workflows.contracts.policy import CAUSE_SIMULATION_FAILED, failure_report
from codemie.workflows.contracts.settings import ContractSettings
from codemie.workflows.validation.contract_issues import (
    CONTRACT_WARNINGS_CAP,
    ContractIssues,
    build_contract_issues,
    build_contract_rejection,
    contract_line_finder,
    format_config_path,
)
from codemie.workflows.validation.line_lookup import NullYamlLineFinder, YamlLineFinder
from codemie.workflows.validation.models import ContractMeta, WorkflowValidationErrorDetail


def _record(
    reported_as=ReportedAs.WARNING,
    outcome=Outcome.ERROR,
    path="input_schema.topic",
    field="topic",
    message="State 'a' needs 'topic'",
    state="a",
    kind=RecordKind.DECLARED,
    label=None,
    provenance=None,
    strict_source=None,
    location=None,
) -> ContractRecord:
    return ContractRecord(
        kind=kind,
        outcome=outcome,
        reported_as=reported_as,
        consumer_state_id=state,
        path=path,
        location=location or _fixture_location(path, state),
        field=field,
        message=message,
        label=label,
        strict_source=strict_source,
        provenance=provenance or Provenance(),
    )


def _build(*records: ContractRecord, **report_kwargs) -> ContractIssues:
    report = ContractLinkReport(records=list(records), status=ContractAnalysisStatus.ANALYSED, **report_kwargs)
    return build_contract_issues(report, NullYamlLineFinder())


def _fixture_location(path: str, state: str) -> ContractLocation | None:
    """Locations supplied by these formatter fixtures; production derivation is tested separately."""
    if not state or path in {"", "tool_args"} or path.startswith(("context.", "something.")):
        return None
    if path.startswith("input_schema."):
        return ContractLocation.state(state, "input_schema", "properties", path.removeprefix("input_schema."))
    if path.startswith("output_schema"):
        return ContractLocation.state(state, "output_schema")
    segments = tuple(int(part) if part.isdigit() else part for part in re.sub(r"\[(\d+)\]", r".\1", path).split("."))
    if path.startswith("config."):
        return (
            ContractLocation(section=LocationSection.CUSTOM_NODES, owner_id="transform", segments=segments)
            if state == "mapped"
            else None
        )
    if state == "tool_only":
        return ContractLocation(section=LocationSection.TOOLS, owner_id="lookup", segments=segments)
    return ContractLocation.state(state, *segments)


@pytest.mark.parametrize(
    "location, expected",
    [
        (ContractLocation.state("a", "input_schema", "properties", "topic"), "input_schema.properties.topic"),
        (ContractLocation.state("a", "tool_args", "01"), "tool_args.01"),
        (ContractLocation.state("a", "next", "switch", "cases", 1, "condition"), "next.switch.cases.1.condition"),
        (ContractLocation.state("a", "tool_args", "a.b[0]"), "tool_args.a.b[0]"),
        (ContractLocation.state("a"), None),
        (None, None),
    ],
)
def test_config_path_formats_typed_locations(location, expected):
    assert format_config_path(location) == expected


def test_config_path_is_bounded_and_control_free_without_changing_exact_line_lookup():
    from codemie.workflows.contracts.policy import MAX_PATH_LENGTH

    key = "ключ<>&\x01\x7f\x85\u2028\u2029" + "x" * 250
    location = ContractLocation.state("a", "tool_args", key)
    source = f"states:\n- id: a\n  tool_args:\n    {json.dumps(key)}: value\n"
    finder = contract_line_finder(SimpleNamespace(yaml_config=source))
    record = _record(location=location)
    report = ContractLinkReport(records=[record], status=ContractAnalysisStatus.ANALYSED)
    entry = build_contract_issues(report, finder).warnings[0]

    assert entry["config_path"] == ("tool_args.ключ<>&" + "x" * 250)[:MAX_PATH_LENGTH]
    assert entry["config_line"] == 4  # exact escaped YAML key, not its shortened public path
    assert record.location.segments[-1] == key


def test_workflow_root_location_does_not_add_a_public_config_section():
    report = failure_report(ContractSettings(mode=ContractValidationMode.STRICT), CAUSE_SIMULATION_FAILED)
    assert report.records[0].location == ContractLocation.workflow()
    entry = build_contract_issues(report, NullYamlLineFinder()).errors[0]
    assert "config_section" not in entry
    assert "config_path" not in entry


def test_entry_carries_the_closed_set_of_keys():
    issues = _build(
        _record(
            provenance=Provenance(producers=("p",)),
            label="certain runtime failure",
        )
    )

    entry = issues.warnings[0]

    assert entry["state_id"] == "a"
    assert entry["path"] == "topic"
    assert entry["config_path"] == "input_schema.properties.topic"
    assert entry["outcome"] == "WARNING"
    assert entry["message"] == "State 'a' needs 'topic'"
    assert entry["kind"] == "contract_validation"
    assert entry["meta"] == {"label": "certain runtime failure"}
    assert "config_line" not in entry
    # every entry is a valid detail once the extra warning kind is set aside
    WorkflowValidationErrorDetail(**{key: value for key, value in entry.items() if key != "kind"})


def test_path_is_empty_string_when_the_record_has_no_field():
    issues = _build(_record(reported_as=ReportedAs.BLOCKS, path="", field=None, state=""))

    entry = issues.errors[0]
    assert entry["path"] == ""
    assert "config_path" not in entry
    assert "state_id" not in entry


@pytest.mark.parametrize(
    "reported_as, expected",
    [(ReportedAs.BLOCKS, "ERROR"), (ReportedAs.WARNING, "WARNING"), (ReportedAs.INFO, "UNVERIFIED")],
)
def test_outcome_is_mapped_from_reported_as(reported_as, expected):
    issues = _build(_record(reported_as=reported_as, outcome=Outcome.UNVERIFIED))

    entries = issues.errors or issues.warnings
    assert [entry["outcome"] for entry in entries] == [expected]


def test_strict_unverified_arrives_as_error_in_errors():
    issues = _build(_record(reported_as=ReportedAs.BLOCKS, outcome=Outcome.UNVERIFIED))

    assert [entry["outcome"] for entry in issues.errors] == ["ERROR"]
    assert issues.warnings == []


def test_warnings_never_hold_an_error_outcome():
    issues = _build(
        _record(reported_as=ReportedAs.WARNING, outcome=Outcome.ERROR),
        _record(reported_as=ReportedAs.INFO, outcome=Outcome.UNVERIFIED, message="other"),
        _record(reported_as=ReportedAs.BLOCKS, outcome=Outcome.ERROR, message="blocking"),
    )

    assert all(entry["outcome"] != "ERROR" for entry in issues.warnings)
    assert [entry["outcome"] for entry in issues.warnings] == ["WARNING", "UNVERIFIED"]
    assert [entry["message"] for entry in issues.errors] == ["blocking"]


def test_ok_records_are_excluded():
    issues = _build(_record(reported_as=ReportedAs.INFO, outcome=Outcome.OK))

    assert issues.errors == []
    assert issues.warnings == []


def test_details_is_omitted_for_empty_provenance():
    issues = _build(_record())

    assert "details" not in issues.warnings[0]


def test_details_is_the_provenance_line_and_never_the_message():
    provenance = Provenance(
        producers=("p1", "p2"),
        removed_by="remover",
        mechanism="clears",
        overwritten_by="writer",
        sources_checked=("s1",),
    )
    issues = _build(_record(provenance=provenance, message="State 'a' needs 'topic'"))

    entry = issues.warnings[0]
    assert entry["details"] == (
        "producers: p1, p2; removed by: remover (clears); overwritten by: writer; sources checked: s1"
    )
    assert entry["message"] not in entry["details"]


def test_details_lists_only_the_parts_that_are_set():
    issues = _build(_record(provenance=Provenance(sources_checked=("s1", "s2"))))

    assert issues.warnings[0]["details"] == "sources checked: s1, s2"


def test_meta_label_is_absent_without_a_label():
    issues = _build(_record())

    assert "meta" not in issues.warnings[0]


@pytest.mark.parametrize("source", [StrictSource.WORKFLOW, StrictSource.STATE])
def test_meta_carries_strict_source_without_a_label(source):
    issues = _build(_record(reported_as=ReportedAs.BLOCKS, strict_source=source))

    assert issues.errors[0]["meta"] == {"strict_source": source.value}


def test_meta_carries_label_and_strict_source_together():
    issues = _build(
        _record(reported_as=ReportedAs.BLOCKS, label="certain runtime failure", strict_source=StrictSource.STATE)
    )

    assert issues.errors[0]["meta"] == {"label": "certain runtime failure", "strict_source": "state"}


def test_meta_has_no_strict_source_when_the_record_does_not_set_it():
    issues = _build(_record(reported_as=ReportedAs.BLOCKS, label="certain runtime failure"))

    assert issues.errors[0]["meta"] == {"label": "certain runtime failure"}


def test_strict_source_is_kept_by_the_minimal_entry():
    broken = _record(reported_as=ReportedAs.BLOCKS, strict_source=StrictSource.WORKFLOW).model_copy(
        update={"label": 123}
    )

    issues = _build(broken)

    assert issues.errors[0]["meta"] == {"strict_source": "workflow"}


def test_rejection_json_carries_strict_source_on_a_graph_level_failure():
    report = failure_report(ContractSettings(mode=ContractValidationMode.STRICT), CAUSE_SIMULATION_FAILED)

    json_error, _ = build_contract_rejection(report, NullYamlLineFinder())

    assert json_error["errors"][0]["meta"] == {"label": CAUSE_SIMULATION_FAILED, "strict_source": "workflow"}


def test_ids_are_identical_across_calls_and_differ_per_message():
    records = [_record(message="one"), _record(message="two")]

    first = _build(*records)
    second = _build(*records)

    assert [entry["id"] for entry in first.warnings] == [entry["id"] for entry in second.warnings]
    assert first.warnings[0]["id"] != first.warnings[1]["id"]


def test_ids_differ_for_records_that_differ_only_by_field_or_producer():
    by_producer = [
        _record(provenance=Provenance(producers=("p1",))),
        _record(provenance=Provenance(producers=("p2",))),
    ]
    by_field = [_record(field="topic"), _record(field="other")]

    for records in (by_producer, by_field):
        issues = _build(*records)
        assert issues.warnings[0]["id"] != issues.warnings[1]["id"]


def test_warnings_are_capped_and_the_cut_is_counted():
    records = [_record(message=f"warning {index}") for index in range(150)]

    issues = _build(*records)

    assert CONTRACT_WARNINGS_CAP == 100
    assert len(issues.warnings) == 100
    assert issues.warnings_truncated == 50
    assert issues.warnings[0]["message"] == "warning 0"


def test_warnings_at_the_cap_are_not_truncated():
    issues = _build(*[_record(message=f"warning {index}") for index in range(100)])

    assert len(issues.warnings) == 100
    assert issues.warnings_truncated == 0


def test_errors_are_not_capped():
    issues = _build(*[_record(reported_as=ReportedAs.BLOCKS, message=f"error {index}") for index in range(120)])

    assert len(issues.errors) == 120


def test_coverage_maps_the_report_counters():
    issues = _build(
        validated_count=3,
        reachable_total=5,
        uncovered_state_ids=["x", "y"],
        cap_reached=True,
    )

    assert issues.coverage == {
        "validated_states": 3,
        "total_states": 5,
        "uncovered_state_ids": ["x", "y"],
        "analysed": True,
        "cap_reached": True,
    }


def test_failure_report_coverage_is_not_analysed():
    report = failure_report(ContractSettings(), CAUSE_SIMULATION_FAILED)

    issues = build_contract_issues(report, NullYamlLineFinder())

    assert issues.coverage["analysed"] is False
    assert issues.coverage["validated_states"] == 0
    assert issues.coverage["total_states"] == 0
    assert [entry["outcome"] for entry in issues.warnings] == ["UNVERIFIED"]
    assert issues.warnings[0]["path"] == ""


def test_coverage_failure_degrades_to_not_analysed_with_zero_counts():
    report = ContractLinkReport.model_construct(
        records=[_record()],
        status=ContractAnalysisStatus.ANALYSED,
        cap_reached=False,
        validated_count=1,
        reachable_total=1,
        uncovered_state_ids=None,  # not iterable: building the coverage dict raises
        unreachable_state_ids=[],
        effective_modes={},
    )

    issues = build_contract_issues(report, NullYamlLineFinder())

    assert issues.coverage == {
        "validated_states": 0,
        "total_states": 0,
        "uncovered_state_ids": [],
        "analysed": False,
        "cap_reached": False,
    }
    assert len(issues.warnings) == 1


def test_broken_provenance_drops_only_details():
    broken = _record().model_copy(update={"provenance": None})

    issues = _build(broken)

    entry = issues.warnings[0]
    assert "details" not in entry
    assert entry["config_path"] == "input_schema.properties.topic"
    assert entry["message"] == "State 'a' needs 'topic'"


def test_record_that_cannot_be_shaped_degrades_to_the_minimal_entry():
    broken = _record().model_copy(update={"label": 123})  # ContractMeta(label=123) is invalid
    healthy = _record(message="healthy")

    issues = _build(broken, healthy)

    assert issues.warnings[0].keys() == {"id", "state_id", "path", "outcome", "message", "kind"}
    assert issues.warnings[0]["outcome"] == "WARNING"
    assert issues.warnings[1]["message"] == "healthy"
    assert "config_path" in issues.warnings[1]


def test_blocking_set_does_not_depend_on_shaping():
    broken = _record(reported_as=ReportedAs.BLOCKS).model_copy(update={"label": 123})

    issues = _build(broken)

    assert len(issues.errors) == 1
    assert issues.errors[0]["outcome"] == "ERROR"


def test_contract_meta_is_part_of_the_detail_model():
    detail = WorkflowValidationErrorDetail(
        id="1", message="m", path="p", meta=ContractMeta(label="l"), config_path="task", outcome="ERROR"
    )

    assert detail.model_dump(exclude_none=True)["meta"] == {"label": "l"}
    assert detail.config_path == "task"


# --- config_line against a real YAML (AC-10) ----------------------------------------------------------

# One state per row of the config_path table. `assistants[].model` is a base name, so the resource stage would re-dump
# the YAML from its objects; the lines must keep following this client text, not the re-dump.
CLIENT_YAML = """\
assistants:
  - id: asst
    model: gpt-4o
custom_nodes:
  - id: transform
    custom_node_id: state_processor
    config:
      input_key: payload
      mappings:
        - source_path: first
          target_key: a
        - source_path: second.deep
          target_key: b
states:
  - id: declared
    assistant_id: asst
    task: |
      Summarise {{topic}}
    input_schema:
      type: object
      properties:
        topic:
          type: string
    output_schema: '{"type": "object"}'
    next:
      state_id: declared_json
  - id: declared_json
    assistant_id: asst
    input_schema: '{"type": "object", "properties": {"topic": {"type": "string"}}}'
    next:
      state_id: bound
  - id: bound
    tool_id: lookup
    tool_args:
      query: "{{topic}}"
    next:
      state_id: mapped
  - id: mapped
    custom_node_id: transform
    next:
      state_id: branching
  - id: branching
    assistant_id: asst
    next:
      condition:
        expression: "topic == 'x'"
        then: switching
        otherwise: switching
  - id: switching
    assistant_id: asst
    next:
      switch:
        cases:
          - condition: "topic == 'a'"
            state_id: iterating
          - condition: "topic == 'b'"
            state_id: iterating
        default: iterating
  - id: iterating
    assistant_id: asst
    next:
      state_id: end
      iter_key: items
"""


def _marker_line(marker: str) -> int:
    """The 1-indexed line of the one CLIENT_YAML line that contains the marker."""
    lines = [number for number, text in enumerate(CLIENT_YAML.splitlines(), start=1) if marker in text]
    assert len(lines) == 1, f"marker {marker!r} must match exactly one line, matched {lines}"
    return lines[0]


def _client_config(source: str | None = CLIENT_YAML):
    parsed = yaml.safe_load(CLIENT_YAML)
    parsed["assistants"][0]["model"] = "gpt-4o-2024-08-06"  # what the rewrite from the objects would write
    return SimpleNamespace(_source_yaml_config=source, yaml_config=yaml.safe_dump(parsed, sort_keys=False))


def _line_of(record_path: str, state: str) -> int | None:
    record = _record(path=record_path, state=state, field=record_path.rsplit(".", 1)[-1])
    entry = build_contract_issues(
        ContractLinkReport(records=[record], status=ContractAnalysisStatus.ANALYSED),
        contract_line_finder(_client_config()),
    ).warnings[0]
    return entry.get("config_line")


# A block-mapping value is reported by the line map at its first body line, so the two declared-mapping rows
# resolve to the first line under the key.
@pytest.mark.parametrize(
    "record_path, state, marker",
    [
        ("input_schema.topic", "declared", "type: string"),
        ("input_schema.topic", "declared_json", "input_schema: '{"),
        ("input_schema", "declared_json", "input_schema: '{"),
        ("input_schema", "declared", "type: object"),
        ("tool_args.query", "bound", "query:"),
        ("config.mappings[1].source_path", "mapped", "source_path: second.deep"),
        ("config.mappings[0].source_path", "mapped", "source_path: first"),
        ("config.input_key", "mapped", "input_key: payload"),
        ("output_schema", "declared", "output_schema:"),
        ("output_schema.summary", "declared", "output_schema:"),
        ("task", "declared", "task: |"),
        ("next.condition.expression", "branching", "expression:"),
        ("next.switch.cases[0].condition", "switching", "topic == 'a'"),
        ("next.switch.cases[1].condition", "switching", "topic == 'b'"),
        ("next.iter_key", "iterating", "iter_key:"),
    ],
)
def test_config_line_is_the_rows_own_line_in_the_client_yaml(record_path, state, marker):
    assert _line_of(record_path, state) == _marker_line(marker)


def test_config_line_never_lands_on_the_states_fallback_line():
    for record_path, state in [("task", "declared"), ("tool_args.query", "bound"), ("next.iter_key", "iterating")]:
        fallback = next(
            number for number, text in enumerate(CLIENT_YAML.splitlines(), start=1) if text.strip() == f"- id: {state}"
        )
        assert _line_of(record_path, state) != fallback


def test_config_line_follows_the_source_text_not_the_rewritten_one():
    config = _client_config()
    rewritten = YamlLineFinder.from_yaml(config.yaml_config, yaml.safe_load(config.yaml_config))

    assert config.yaml_config != CLIENT_YAML
    assert contract_line_finder(config).find_line_for_state_field("bound", "tool_args.query", allow_fallback=False) == (
        _marker_line("query:")
    )
    assert rewritten.find_line_for_state_field("bound", "tool_args.query", allow_fallback=False) != _marker_line(
        "query:"
    )


def test_line_finder_falls_back_to_the_yaml_config_without_a_source_text():
    config = SimpleNamespace(_source_yaml_config=None, yaml_config=CLIENT_YAML)

    finder = contract_line_finder(config)

    assert finder.find_line_for_state_field("bound", "tool_args.query") == _marker_line("query:")


@pytest.mark.parametrize(
    "record_path, state",
    [
        ("context.topic", "bound"),  # outside the table: no config_path, so no line
        ("", "bound"),
        ("tool_args.query", "no_such_state"),
        ("tool_args.query", "declared"),  # state has no tool_args at all
        ("config.mappings[0].source_path", "bound"),  # state has no custom node
        ("config.mappings[0].source_path", ""),
    ],
)
def test_unknown_path_gives_no_config_line(record_path, state):
    assert _line_of(record_path, state) is None


TOOL_ONLY_YAML = """\
assistants:
  - id: asst
    model: gpt-4o
tools:
  - id: lookup
    tool: generic
    tool_args:
      query: "{{topic}}"
states:
  - id: tool_only
    tool_id: lookup
    next:
      state_id: state_wins
  - id: state_wins
    tool_id: lookup
    tool_args:
      query: "{{topic}}"
    next:
      state_id: end
"""


def _tool_args_entry(state: str, source: str = TOOL_ONLY_YAML) -> dict:
    parsed = yaml.safe_load(source)
    finder = YamlLineFinder.from_yaml(source, parsed)
    record = _record(path="tool_args.query", state=state, field="query")
    return build_contract_issues(
        ContractLinkReport(records=[record], status=ContractAnalysisStatus.ANALYSED), finder
    ).warnings[0]


def test_tool_only_bindings_are_located_in_the_tools_section():
    entry = _tool_args_entry("tool_only")

    assert entry["config_path"] == "tool_args.query"
    assert entry["config_section"] == "tools"
    assert TOOL_ONLY_YAML.splitlines()[entry["config_line"] - 1].strip().startswith("query:")
    assert entry["config_line"] < TOOL_ONLY_YAML.splitlines().index("states:") + 1  # inside tools[], not states[]


def test_state_bindings_win_over_the_tools_like_the_linker_reads_them():
    entry = _tool_args_entry("state_wins")

    assert entry["config_section"] == "states"
    assert entry["config_line"] > TOOL_ONLY_YAML.splitlines().index("states:") + 1


@pytest.mark.parametrize(
    "record_path, state, section",
    [
        ("input_schema.topic", "declared", "states"),
        ("task", "declared", "states"),
        ("config.input_key", "mapped", "custom_nodes"),
        ("config.mappings[0].source_path", "mapped", "custom_nodes"),
    ],
)
def test_config_section_names_the_section_that_owns_the_path(record_path, state, section):
    record = _record(path=record_path, state=state, field=record_path.rsplit(".", 1)[-1])
    finder = contract_line_finder(_client_config())

    entry = build_contract_issues(
        ContractLinkReport(records=[record], status=ContractAnalysisStatus.ANALYSED), finder
    ).warnings[0]

    assert entry["config_section"] == section


def test_a_path_outside_the_table_has_no_config_section():
    assert "config_section" not in _build(_record(path="context.topic")).warnings[0]


def test_issues_of_different_non_ascii_fields_get_different_ids_and_their_own_paths():
    first = _build(_record(path="input_schema.имя", field="имя")).warnings[0]
    second = _build(_record(path="input_schema.дом", field="дом")).warnings[0]

    assert first["id"] != second["id"]
    assert (first["path"], second["path"]) == ("имя", "дом")
    assert first["config_path"] == "input_schema.properties.имя"


def test_a_field_name_with_a_space_points_at_the_key_that_exists():
    entry = _build(_record(path="input_schema.user name", field="user name")).warnings[0]

    assert entry["path"] == "user name"
    assert entry["config_path"] == "input_schema.properties.user name"


def test_unparseable_yaml_gives_a_null_line_finder():
    finder = contract_line_finder(SimpleNamespace(_source_yaml_config="a: [unclosed\n - : :", yaml_config=""))

    assert isinstance(finder, NullYamlLineFinder)


@pytest.mark.parametrize("text", ["- just\n- a list\n", "", None])
def test_yaml_that_is_not_a_mapping_gives_a_null_line_finder(text):
    assert isinstance(
        contract_line_finder(SimpleNamespace(_source_yaml_config=None, yaml_config=text)), NullYamlLineFinder
    )


def test_object_without_yaml_gives_a_null_line_finder():
    assert isinstance(contract_line_finder(object()), NullYamlLineFinder)


# ---------------------------------------------------------------------------------------------------------------------
# build_contract_rejection
# ---------------------------------------------------------------------------------------------------------------------


def _rejection(*records: ContractRecord, **report_kwargs):
    report = ContractLinkReport(records=list(records), status=ContractAnalysisStatus.ANALYSED, **report_kwargs)
    return build_contract_rejection(report, NullYamlLineFinder())


def _blocking(**kwargs) -> ContractRecord:
    return _record(reported_as=ReportedAs.BLOCKS, **kwargs)


def test_rejection_is_none_without_blocking_records():
    assert _rejection() is None
    assert _rejection(_record(reported_as=ReportedAs.WARNING)) is None
    assert build_contract_rejection(ContractLinkReport(), NullYamlLineFinder()) is None


def test_rejection_json_carries_errors_warnings_coverage_and_the_old_envelope():
    json_error, _ = _rejection(
        _blocking(message="first"),
        _blocking(message="second", state="b"),
        _record(reported_as=ReportedAs.INFO, outcome=Outcome.UNVERIFIED, message="unsure"),
        validated_count=2,
        reachable_total=4,
    )

    assert set(json_error) == {"error_type", "message", "errors", "warnings", "coverage"}
    assert json_error["error_type"] == "contract_validation"
    assert json_error["message"] == "Workflow contracts are not satisfied"
    assert [entry["message"] for entry in json_error["errors"]] == ["first", "second"]
    assert [entry["outcome"] for entry in json_error["warnings"]] == ["UNVERIFIED"]
    assert json_error["coverage"]["analysed"] is True
    assert (json_error["coverage"]["validated_states"], json_error["coverage"]["total_states"]) == (2, 4)
    assert {"id", "message", "path", "state_id"} <= set(json_error["errors"][0])
    assert json_error["errors"][0]["path"] == "topic"


def test_rejection_string_lists_errors_then_prefixed_warnings_then_coverage():
    _, text = _rejection(
        _blocking(message="first"),
        _record(reported_as=ReportedAs.WARNING, message="derived"),
        _record(reported_as=ReportedAs.INFO, outcome=Outcome.UNVERIFIED, message="unsure"),
        validated_count=2,
        reachable_total=4,
    )

    lines = text.split("<br>")
    assert "\n" not in text
    assert lines.index("first") < lines.index("WARNING: derived") < lines.index("UNVERIFIED: unsure")
    assert lines.index("UNVERIFIED: unsure") < lines.index("Contracts checked on 2 of 4 reachable states")
    assert text.count("Contracts checked on") == 1


def test_rejection_keeps_the_label_on_the_error_line():
    _, text = _rejection(_blocking(message="boom", label="certain runtime failure"))

    assert "boom (certain runtime failure)" in text


def test_rejection_without_truncation_has_no_warnings_truncated():
    json_error, text = _rejection(_blocking(), _record(message="w"))

    assert "warnings_truncated" not in json_error
    assert "warnings_truncated" not in text


def test_rejection_with_150_warnings_reports_the_cut_in_both_formats():
    records = [_blocking()] + [_record(message=f"warning {index}") for index in range(150)]

    json_error, text = _rejection(*records)

    assert len(json_error["warnings"]) == 100
    assert json_error["warnings_truncated"] == 50
    assert "warnings_truncated: 50" in text.split("<br>")
    assert text.count("WARNING:") == 100


@pytest.mark.parametrize("failed_helpers", [("_detail", "_minimal"), ("_issue_id",)])
def test_final_fallback_is_independent_serializable_and_keeps_classification(failed_helpers):
    from contextlib import ExitStack

    records = [
        _blocking(message="first", strict_source=StrictSource.WORKFLOW),
        _blocking(message="second"),
        _record(reported_as=ReportedAs.WARNING),
        _record(reported_as=ReportedAs.INFO),
    ]
    with ExitStack() as stack:
        for helper in failed_helpers:
            stack.enter_context(
                patch(
                    f"codemie.workflows.validation.contract_issues.{helper}",
                    side_effect=ValueError("PRIVATE EXCEPTION"),
                )
            )
        payload, _ = _rejection(*records)
    assert [entry["outcome"] for entry in payload["errors"]] == ["ERROR", "ERROR"]
    assert [entry["outcome"] for entry in payload["warnings"]] == ["WARNING", "UNVERIFIED"]
    entries = payload["errors"] + payload["warnings"]
    assert len({entry["id"] for entry in entries}) == 4
    assert all("meta" not in entry for entry in entries)
    assert "PRIVATE EXCEPTION" not in json.dumps(payload)
    assert all(entry["message"] == "Workflow contract issue details are unavailable" for entry in entries)
