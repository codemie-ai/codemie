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

"""Linker entry points (EPMCDME-16503, T8): link_workflow_contracts, validate_workflow_with_report and its wiring.

Covers AC-16 with the linker off, AC-18/AC-19 reject and save paths in both error formats, error collection (every
contract error in one ValueError), per-call reports under concurrency and the import direction of `contracts/`.
"""

import ast
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from codemie.core.workflow_models import WorkflowConfig
from codemie.workflows.constants import ContractValidationMode, WorkflowErrorType
from codemie.workflows.contracts import linker as linker_module
from codemie.workflows.contracts.linker import link_workflow_contracts
from codemie.workflows.contracts.models import ContractAnalysisStatus, ContractLinkReport, ReportedAs
from codemie.workflows.contracts.policy import CAUSE_SETTINGS_FAILED, CAUSE_SIMULATION_FAILED
from codemie.workflows.contracts.simulate import simulate_workflow
from codemie.workflows.validation.contract_issues import build_contract_rejection
from codemie.workflows.validation.line_lookup import NullYamlLineFinder
from codemie.workflows.validation.tool_schemas import SaveTimeToolSchemaResolver
from codemie.workflows.workflow import WorkflowExecutor

CONTRACTS_DIR = Path(__file__).resolve().parents[4] / "src" / "codemie" / "workflows" / "contracts"

PRODUCER = '{"type":"object","properties":{"x":{"type":"string"}},"required":["x"]}'
NEEDS_X = '{"type":"object","properties":{"x":{"type":"string"}},"required":["x"]}'
NEEDS_DOC = '{"type":"object","properties":{"doc":{"type":"string"}},"required":["doc"]}'
EMPTY_CONTRACT = '{"type":"object","properties":{}}'


def _assistants() -> list[dict]:
    return [{"id": "a1", "system_prompt": "do it"}]


def _clean() -> dict:
    return {
        "assistants": _assistants(),
        "states": [
            {"id": "s1", "assistant_id": "a1", "next": {"state_id": "s2"}},
            {"id": "s2", "assistant_id": "a1", "next": {"state_id": "end"}},
        ],
    }


def _removed_then_needed(consumers: int = 1, **extra) -> dict:
    """s1 produces x, s2 removes it, and every following consumer declares a need for x: one declared ERROR each."""
    states = [
        {"id": "s1", "assistant_id": "a1", "output_schema": PRODUCER, "next": {"state_id": "s2"}},
        {
            "id": "s2",
            "assistant_id": "a1",
            "next": {"state_id": "c1", "reset_keys_in_context_store": ["x"]},
        },
    ]
    for index in range(1, consumers + 1):
        target = f"c{index + 1}" if index < consumers else "end"
        states.append(
            {
                "id": f"c{index}",
                "assistant_id": "a1",
                "input_schema": NEEDS_X,
                "output_schema": f'{{"type":"object","properties":{{"y{index}":{{"type":"string"}}}}}}',
                "next": {"state_id": target},
            }
        )
    return {"assistants": _assistants(), "states": states, **extra}


def _derived_error() -> dict:
    """The same removal, read through a placeholder: a derived ERROR, which saves in the default mode."""
    config = _removed_then_needed()
    for state in config["states"]:
        if state["id"] == "c1":
            del state["input_schema"]
            state["task"] = "use {{ x }}"
            state["resolve_dynamic_values_in_prompt"] = True
    return config


def _unverified_need() -> dict:
    """A declared need no source can confirm: UNVERIFIED, info in warn and blocks in strict."""
    config = _clean()
    config["states"][1]["input_schema"] = NEEDS_DOC
    return config


def _stored(config_dict: dict) -> WorkflowConfig:
    return WorkflowConfig(
        id="wf-1",
        name="wf",
        description="d",
        project="demo",
        mode="Sequential",
        yaml_config=yaml.safe_dump(config_dict),
    )


def _parsed(config_dict: dict) -> WorkflowConfig:
    config = _stored(config_dict)
    config.parse_execution_config()
    return config


def _validate(config_dict: dict, *, link_contracts: bool, error_format: str = "string"):
    return WorkflowExecutor.validate_workflow_with_report(
        _stored(config_dict), MagicMock(id="u1"), error_format=error_format, link_contracts=link_contracts
    )


# ---------------------------------------------------------------------------------------------------------------------
# link_workflow_contracts
# ---------------------------------------------------------------------------------------------------------------------


def test_link_workflow_contracts_returns_an_analysed_report_with_blocking_records():
    report = link_workflow_contracts(_parsed(_removed_then_needed()))

    assert isinstance(report, ContractLinkReport)
    assert report.status is ContractAnalysisStatus.ANALYSED
    assert report.reachable_total == 3
    blocking = report.blocking_records()
    assert [(r.consumer_state_id, r.path) for r in blocking] == [("c1", "input_schema.x")]


def test_link_workflow_contracts_reads_the_workflow_mode_from_the_yaml():
    report = link_workflow_contracts(_parsed({**_unverified_need(), "save_validation": "strict"}))

    assert {state: mode.value for state, mode in report.effective_modes.items()} == {"s1": "strict", "s2": "strict"}
    assert [r.consumer_state_id for r in report.blocking_records()] == ["s2"]


def test_link_workflow_contracts_reports_are_independent_values():
    config = _parsed(_removed_then_needed())

    first = link_workflow_contracts(config)
    with pytest.raises(AttributeError):
        first.records.clear()  # a tuple: a caller cannot empty the report for the next one
    second = link_workflow_contracts(config)

    assert first.blocking_records()
    assert second.blocking_records()
    assert second is not first


def test_link_workflow_contracts_takes_the_resolver_as_a_defaulted_argument():
    resolver = MagicMock()
    resolver.mandatory_params.return_value = None

    with patch.object(linker_module, "simulate_workflow", wraps=linker_module.simulate_workflow) as simulate:
        link_workflow_contracts(_parsed(_clean()))
        link_workflow_contracts(_parsed(_clean()), resolver=resolver)

    assert simulate.call_args_list[0].kwargs["resolver"] is None
    assert simulate.call_args_list[1].kwargs["resolver"] is resolver


def test_link_workflow_contracts_turns_a_simulation_crash_into_one_graph_level_record():
    with patch.object(linker_module, "simulate_workflow", side_effect=RuntimeError("boom")):
        report = link_workflow_contracts(_parsed(_clean()))

    assert report.status is ContractAnalysisStatus.FAILED
    assert [(r.consumer_state_id, r.label) for r in report.records] == [("", CAUSE_SIMULATION_FAILED)]
    assert report.blocking_records() == []
    assert "boom" not in report.to_json()


def test_link_workflow_contracts_simulation_crash_blocks_only_in_strict():
    with patch.object(linker_module, "simulate_workflow", side_effect=RuntimeError("boom")):
        report = link_workflow_contracts(_parsed({**_clean(), "save_validation": "strict"}))

    assert [r.reported_as for r in report.records] == [ReportedAs.BLOCKS]


def test_link_workflow_contracts_fails_closed_when_the_settings_reader_fails():
    with patch.object(linker_module, "read_contract_settings", side_effect=RuntimeError("boom")):
        report = link_workflow_contracts(_parsed({**_clean(), "save_validation": "strict"}))

    assert isinstance(report, ContractLinkReport)
    assert report.status is ContractAnalysisStatus.FAILED
    assert [(r.consumer_state_id, r.label, r.reported_as) for r in report.records] == [
        ("", CAUSE_SETTINGS_FAILED, ReportedAs.BLOCKS)
    ]
    # strict is assumed here (fail closed), not set by the author, so the record does not claim a strict source
    assert report.records[0].strict_source is None
    assert "boom" not in report.to_json()


# ---------------------------------------------------------------------------------------------------------------------
# validate_workflow_with_report: flag off
# ---------------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("config_dict", [_clean(), _removed_then_needed(), _derived_error(), _unverified_need()])
def test_flag_off_runs_no_analysis_and_returns_an_empty_report(config_dict):
    with patch("codemie.workflows.workflow.link_workflow_contracts") as linker:
        graph, report = _validate(config_dict, link_contracts=False)

    linker.assert_not_called()
    assert graph is not None
    assert report == ContractLinkReport()
    assert report.status is ContractAnalysisStatus.NOT_RUN


def test_flag_defaults_to_off():
    with patch("codemie.workflows.workflow.link_workflow_contracts") as linker:
        WorkflowExecutor.validate_workflow_with_report(_stored(_removed_then_needed()), MagicMock(id="u1"))
        WorkflowExecutor.validate_workflow(_stored(_removed_then_needed()), MagicMock(id="u1"))

    linker.assert_not_called()


@pytest.mark.parametrize("error_format", ["json", "string"])
def test_flag_off_ac16_still_rejects_a_bad_input_schema(error_format):
    config_dict = _clean()
    config_dict["states"][1]["input_schema"] = '{"doc":"Title"}'

    with patch("codemie.workflows.workflow.link_workflow_contracts") as linker:
        with pytest.raises(ValueError) as exc_info:
            _validate(config_dict, link_contracts=False, error_format=error_format)

    linker.assert_not_called()
    if error_format == "json":
        error = exc_info.value.args[0]
        assert error["error_type"] == WorkflowErrorType.SCHEMA_VALIDATION.value
    else:
        assert "input_schema" in str(exc_info.value)


@pytest.mark.parametrize("error_format", ["json", "string"])
def test_flag_on_ac16_is_still_a_schema_validation_error_and_the_linker_does_not_run(error_format):
    config_dict = _clean()
    config_dict["states"][1]["input_schema"] = '{"doc":"Title"}'

    with patch("codemie.workflows.workflow.link_workflow_contracts") as linker:
        with pytest.raises(ValueError) as exc_info:
            _validate(config_dict, link_contracts=True, error_format=error_format)

    linker.assert_not_called()
    if error_format == "json":
        assert exc_info.value.args[0]["error_type"] == WorkflowErrorType.SCHEMA_VALIDATION.value


@pytest.mark.parametrize("malformed", ["malformed", 7, None, ["s3"]], ids=["string", "int", "null", "list"])
@pytest.mark.parametrize("link_contracts", [False, True], ids=["linking-off", "linking-on"])
@pytest.mark.parametrize("error_format", ["json", "string"])
def test_a_non_object_state_is_a_controlled_schema_validation_error(error_format, link_contracts, malformed):
    config_dict = _clean()
    config_dict["states"].append(malformed)

    with patch("codemie.workflows.workflow.link_workflow_contracts") as linker:
        with pytest.raises(ValueError) as exc_info:
            _validate(config_dict, link_contracts=link_contracts, error_format=error_format)

    linker.assert_not_called()
    if error_format == "json":
        error = exc_info.value.args[0]
        assert error["error_type"] == WorkflowErrorType.SCHEMA_VALIDATION.value
        type_errors = [detail for detail in error["errors"] if detail["message"] == "Invalid type (expected object)"]
        assert [(detail["path"], "state_id" in detail) for detail in type_errors] == [("states", False)]
        assert type_errors[0]["config_line"] > 0
    else:
        assert f"In 'states[{len(config_dict['states']) - 1}]': object is expected" in str(exc_info.value)


@pytest.mark.parametrize("link_contracts", [False, True], ids=["linking-off", "linking-on"])
def test_a_non_object_state_keeps_the_id_of_a_sibling_state_error(link_contracts):
    config_dict = _clean()
    config_dict["states"][1]["input_schema"] = '{"doc":"Title"}'
    config_dict["states"].append("malformed")

    with pytest.raises(ValueError) as exc_info:
        _validate(config_dict, link_contracts=link_contracts, error_format="json")

    details = exc_info.value.args[0]["errors"]
    by_state = {detail.get("state_id"): detail for detail in details}
    assert config_dict["states"][1]["id"] in by_state
    assert None in by_state


@pytest.mark.parametrize("link_contracts", [False, True], ids=["linking-off", "linking-on"])
def test_a_non_object_state_beside_a_bad_assistant_is_still_a_controlled_error(link_contracts):
    config_dict = _clean()
    config_dict["assistants"].append({"id": "bad", "model": 5})
    config_dict["states"].append("malformed")

    with pytest.raises(ValueError) as exc_info:
        _validate(config_dict, link_contracts=link_contracts, error_format="json")

    assert exc_info.value.args[0]["error_type"] == WorkflowErrorType.SCHEMA_VALIDATION.value


# ---------------------------------------------------------------------------------------------------------------------
# validate_workflow_with_report: AC-18 / AC-19
# ---------------------------------------------------------------------------------------------------------------------


def test_ac18_declared_error_rejects_in_json_format_with_the_contract_error_type():
    with pytest.raises(ValueError) as exc_info:
        _validate(_removed_then_needed(), link_contracts=True, error_format="json")

    error = exc_info.value.args[0]
    assert error["error_type"] == WorkflowErrorType.CONTRACT_VALIDATION.value
    assert error["message"]
    assert len(error["errors"]) == 1
    detail = error["errors"][0]
    assert (detail["state_id"], detail["path"]) == ("c1", "x")
    assert "'x'" in detail["message"] and "<" not in detail["message"]
    assert detail["id"]


def test_ac18_declared_error_rejects_in_string_format_with_br_markup():
    with pytest.raises(ValueError) as exc_info:
        _validate(_removed_then_needed(), link_contracts=True, error_format="string")

    text = str(exc_info.value)
    assert isinstance(exc_info.value.args[0], str)
    assert "<br>" in text and "\n" not in text
    assert "'x'" in text and "'c1'" in text


@pytest.mark.parametrize("error_format", ["json", "string"])
def test_every_contract_error_is_collected_into_one_value_error(error_format):
    with pytest.raises(ValueError) as exc_info:
        _validate(_removed_then_needed(consumers=3), link_contracts=True, error_format=error_format)

    if error_format == "json":
        errors = exc_info.value.args[0]["errors"]
        assert [e["state_id"] for e in errors] == ["c1", "c2", "c3"]
    else:
        text = str(exc_info.value)
        assert all(f"'c{index}'" in text for index in (1, 2, 3))


def test_the_string_error_is_neutral_for_hostile_names():
    config_dict = _removed_then_needed()
    config_dict["states"][2]["input_schema"] = (
        '{"type":"object","properties":{"<img src=x onerror=1>":{"type":"string"}},"required":["<img src=x onerror=1>"]}'
    )
    config_dict["states"][1]["next"]["reset_keys_in_context_store"] = ["<img src=x onerror=1>"]
    config_dict["states"][0]["output_schema"] = (
        '{"type":"object","properties":{"<img src=x onerror=1>":{"type":"string"}},"required":["<img src=x onerror=1>"]}'
    )

    with pytest.raises(ValueError) as exc_info:
        _validate(config_dict, link_contracts=True, error_format="string")

    assert "<img" not in str(exc_info.value)
    assert "img_src_x_onerror_1" in str(exc_info.value)
    assert str(exc_info.value).replace("<br>", "").count("<") == 0


def test_ac18_derived_error_saves_in_the_default_mode_and_is_reported_as_a_warning():
    graph, report = _validate(_derived_error(), link_contracts=True)

    assert graph is not None
    assert report.status is ContractAnalysisStatus.ANALYSED
    assert report.blocking_records() == []
    derived = [r for r in report.records if r.consumer_state_id == "c1" and r.outcome.value == "ERROR"]
    assert [r.reported_as for r in derived] == [ReportedAs.WARNING]


def test_ac18_unverified_findings_save_in_the_default_mode():
    _, report = _validate(_unverified_need(), link_contracts=True)

    assert report.status is ContractAnalysisStatus.ANALYSED
    assert report.blocking_records() == []
    assert [r.reported_as for r in report.records if r.outcome.value == "UNVERIFIED"] == [ReportedAs.INFO]


@pytest.mark.parametrize("error_format", ["json", "string"])
def test_ac19_strict_rejects_an_unverified_finding_with_the_contract_error_type(error_format):
    with pytest.raises(ValueError) as exc_info:
        _validate({**_unverified_need(), "save_validation": "strict"}, link_contracts=True, error_format=error_format)

    if error_format == "json":
        error = exc_info.value.args[0]
        assert error["error_type"] == WorkflowErrorType.CONTRACT_VALIDATION.value
        assert [(e["state_id"], e["path"]) for e in error["errors"]] == [("s2", "doc")]
    else:
        assert "'doc'" in str(exc_info.value)


def _two_tool_states(**extra) -> dict:
    return {
        "assistants": _assistants(),
        "tools": [{"id": "t1", "tool": "first_tool"}, {"id": "t2", "tool": "second_tool"}],
        "states": [
            {"id": "s1", "tool_id": "t1", "next": {"state_id": "s2"}},
            {"id": "s2", "tool_id": "t2", "next": {"state_id": "end"}},
        ],
        **extra,
    }


class _EveryToolHasNoMandatoryParameter:
    def mandatory_params(self, tool_ref):
        return frozenset()


def test_a_made_up_tool_name_is_rejected_before_any_schema_lookup():
    # The lookup budget is only a ceiling for real tools: the resource stage stops an unknown name before the resolver
    # starts answering, so thousands of made-up names in a YAML cost no service call.
    config_dict = {
        "assistants": _assistants(),
        "tools": [{"id": f"t{index}", "tool": f"made_up_{index}"} for index in range(300)],
        "states": [{"id": "s1", "tool_id": "t0", "next": {"state_id": "end"}}],
    }

    with (
        patch("codemie.workflows.validation.resources.ToolsService.find_toolkit_for_tool", side_effect=ValueError),
        patch("codemie.workflows.validation.tool_schemas.ToolDiscoveryService.find_tool_by_name") as find_tool,
        patch("codemie.workflows.workflow.link_workflow_contracts") as link,
        pytest.raises(ValueError, match="do not exist"),
    ):
        _validate(config_dict, link_contracts=True)

    find_tool.assert_not_called()
    link.assert_not_called()


def test_an_exhausted_lookup_budget_still_blocks_a_strict_save():
    config = _parsed(_two_tool_states(save_validation="strict"))

    with patch.object(linker_module, "simulate_workflow", partial(simulate_workflow, lookup_budget=1)):
        report = link_workflow_contracts(config, resolver=_EveryToolHasNoMandatoryParameter())

    blocking = report.blocking_records()
    assert [(r.consumer_state_id, r.outcome.value) for r in blocking] == [("s2", "UNVERIFIED")]
    assert "lookup limit" in blocking[0].message


def test_an_exhausted_lookup_budget_only_informs_in_the_default_mode():
    config = _parsed(_two_tool_states())

    with patch.object(linker_module, "simulate_workflow", partial(simulate_workflow, lookup_budget=1)):
        report = link_workflow_contracts(config, resolver=_EveryToolHasNoMandatoryParameter())

    assert report.blocking_records() == []
    assert [(r.consumer_state_id, r.reported_as) for r in report.records if r.outcome.value == "UNVERIFIED"] == [
        ("s2", ReportedAs.INFO)
    ]


# ---------------------------------------------------------------------------------------------------------------------
# Workflow keys read from the YAML, through the enabled validation pipeline (AC-19, AC-21)
# ---------------------------------------------------------------------------------------------------------------------


def _blocking_json(config_dict: dict) -> list[tuple[str, str]]:
    with pytest.raises(ValueError) as exc_info:
        _validate(config_dict, link_contracts=True, error_format="json")
    error = exc_info.value.args[0]
    assert error["error_type"] == WorkflowErrorType.CONTRACT_VALIDATION.value
    return [(e.get("state_id", ""), e["path"]) for e in error["errors"]]


def _covered(config_dict: dict) -> dict:
    """The same workflow with an input contract on every state, so no state is uncovered."""
    for state in config_dict["states"]:
        state.setdefault("input_schema", EMPTY_CONTRACT)
    return config_dict


def test_require_input_contracts_from_yaml_rejects_every_uncovered_state():
    config_dict = {**_clean(), "require_input_contracts": True}

    assert _blocking_json(config_dict) == [("s1", ""), ("s2", "")]


def test_require_input_contracts_from_yaml_saves_when_every_state_declares_a_contract():
    config_dict = _covered({**_clean(), "require_input_contracts": True})

    _, report = _validate(config_dict, link_contracts=True)

    assert report.status is ContractAnalysisStatus.ANALYSED
    assert report.blocking_records() == []


def test_without_require_input_contracts_an_uncovered_state_saves():
    _, report = _validate(_clean(), link_contracts=True)

    assert report.blocking_records() == []


def test_a_non_boolean_require_input_contracts_is_rejected_by_the_schema_stage_before_the_linker():
    config_dict = {**_clean(), "require_input_contracts": "true"}

    with patch("codemie.workflows.workflow.link_workflow_contracts") as linker:
        with pytest.raises(ValueError, match="require_input_contracts"):
            _validate(config_dict, link_contracts=True)

    linker.assert_not_called()


def test_state_level_strict_from_yaml_rejects_in_a_warn_workflow():
    config_dict = _unverified_need()
    config_dict["states"][1]["save_validation"] = "strict"

    assert _blocking_json(config_dict) == [("s2", "doc")]


def test_state_level_strict_on_another_state_leaves_the_unverified_finding_as_info():
    config_dict = _unverified_need()
    config_dict["states"][0]["save_validation"] = "strict"

    _, report = _validate(config_dict, link_contracts=True)

    assert report.blocking_records() == []
    assert [r.reported_as for r in report.records if r.outcome.value == "UNVERIFIED"] == [ReportedAs.INFO]


def test_state_level_warn_from_yaml_overrides_a_strict_workflow():
    config_dict = {**_unverified_need(), "save_validation": "strict"}
    config_dict["states"][1]["save_validation"] = "warn"

    _, report = _validate(config_dict, link_contracts=True)

    assert report.blocking_records() == []


def _two_errors_and_a_warning() -> dict:
    """c1 and c2 each declare a removed `x` (two blocking records); d1 declares an unknown `doc` (one warning)."""
    config = _removed_then_needed(consumers=2)
    config["states"][-1]["next"] = {"state_id": "d1"}
    config["states"].append({"id": "d1", "assistant_id": "a1", "input_schema": NEEDS_DOC, "next": {"state_id": "end"}})
    return config


def test_rejection_in_json_format_carries_errors_warnings_coverage_and_every_old_key():
    with pytest.raises(ValueError) as exc_info:
        _validate(_two_errors_and_a_warning(), link_contracts=True, error_format="json")

    error = exc_info.value.args[0]
    assert error["error_type"] == WorkflowErrorType.CONTRACT_VALIDATION.value
    assert error["message"] == "Workflow contracts are not satisfied"
    assert [e["state_id"] for e in error["errors"]] == ["c1", "c2"]
    assert all({"id", "message", "path", "state_id"} <= set(e) for e in error["errors"])
    assert [e["path"] for e in error["errors"]] == ["x", "x"]
    assert [(w["state_id"], w["path"], w["kind"]) for w in error["warnings"]] == [("d1", "doc", "contract_validation")]
    assert error["coverage"]["analysed"] is True
    assert "warnings_truncated" not in error


def test_rejection_in_string_format_ends_with_the_coverage_line_and_has_no_truncation_line():
    with pytest.raises(ValueError) as exc_info:
        _validate(_two_errors_and_a_warning(), link_contracts=True, error_format="string")

    text = str(exc_info.value)
    lines = text.removesuffix("<br>").split("<br>")
    assert "\n" not in text
    assert any(line.startswith("UNVERIFIED: ") for line in lines)
    assert lines[-1].startswith("Contracts checked on ") and lines[-1].endswith(" reachable states")
    assert "warnings_truncated" not in text


def test_an_earlier_stage_failure_raises_that_stage_and_never_reaches_the_linker():
    config_dict = _clean()
    config_dict["states"] = []

    with patch("codemie.workflows.workflow.link_workflow_contracts") as linker:
        with pytest.raises(ValueError) as exc_info:
            _validate(config_dict, link_contracts=True, error_format="json")

    linker.assert_not_called()
    assert exc_info.value.args[0]["error_type"] == WorkflowErrorType.MISSING_STATES.value


def test_the_executor_shapes_the_rejection_with_the_line_finder_of_the_workflow_config():
    with (
        patch("codemie.workflows.workflow.build_contract_rejection", wraps=build_contract_rejection) as shaping,
        patch("codemie.workflows.workflow.contract_line_finder") as finder,
    ):
        finder.return_value = NullYamlLineFinder()
        with pytest.raises(ValueError):
            _validate(_removed_then_needed(), link_contracts=True, error_format="json")

    assert shaping.call_args.args[1] is finder.return_value
    assert finder.call_args.args[0].yaml_config


# ---------------------------------------------------------------------------------------------------------------------
# The entry node's input contract seeds the initial input (AC-2, AC-3, AC-5)
# ---------------------------------------------------------------------------------------------------------------------


def _entry_workflow(entry_input: str | None) -> dict:
    """s1 (entry, known empty output) feeds s2, which needs `doc`; `entry_input` is s1's declared input contract."""
    entry: dict = {"id": "s1", "assistant_id": "a1", "output_schema": EMPTY_CONTRACT, "next": {"state_id": "s2"}}
    if entry_input is not None:
        entry["input_schema"] = entry_input
    consumer = {"id": "s2", "assistant_id": "a1", "input_schema": NEEDS_DOC, "next": {"state_id": "end"}}
    return {"assistants": _assistants(), "states": [entry, consumer]}


def _doc_record(report: ContractLinkReport, state_id: str):
    return next(r for r in report.records if r.consumer_state_id == state_id and r.path == "input_schema.doc")


def test_a_required_entry_field_is_ok_for_a_downstream_node_and_names_the_entry_input_in_the_linker():
    report = link_workflow_contracts(_parsed(_entry_workflow(NEEDS_DOC)))

    for state_id in ("s1", "s2"):
        record = _doc_record(report, state_id)
        assert (record.outcome.value, record.reported_as) == ("OK", ReportedAs.INFO)
        assert list(record.provenance.producers) == ["initial_input"]
        assert "initial_input" in record.message
    assert report.blocking_records() == []


def test_a_required_entry_field_is_ok_in_the_enabled_validation_pipeline():
    graph, report = _validate({**_entry_workflow(NEEDS_DOC), "save_validation": "strict"}, link_contracts=True)

    assert graph is not None
    assert _doc_record(report, "s2").outcome.value == "OK"
    assert report.blocking_records() == []


def test_the_linker_reads_the_settings_from_the_document_the_schema_stage_parsed():
    with patch("codemie.workflows.contracts.settings._load_document") as load:
        _, report = _validate({**_entry_workflow(NEEDS_DOC), "save_validation": "strict"}, link_contracts=True)

    load.assert_not_called()
    assert set(report.effective_modes.values()) == {ContractValidationMode.STRICT}


def test_an_empty_entry_contract_with_a_missing_downstream_field_is_a_blocking_error_in_the_linker():
    report = link_workflow_contracts(_parsed(_entry_workflow(EMPTY_CONTRACT)))

    record = _doc_record(report, "s2")
    assert (record.outcome.value, record.reported_as) == ("ERROR", ReportedAs.BLOCKS)
    assert "not guaranteed by the contracts" in record.message
    assert report.blocking_records() == [record]


@pytest.mark.parametrize("error_format", ["json", "string"])
def test_an_empty_entry_contract_with_a_missing_downstream_field_is_rejected_by_the_enabled_pipeline(error_format):
    with pytest.raises(ValueError) as exc_info:
        _validate(_entry_workflow(EMPTY_CONTRACT), link_contracts=True, error_format=error_format)

    if error_format == "json":
        error = exc_info.value.args[0]
        assert error["error_type"] == WorkflowErrorType.CONTRACT_VALIDATION.value
        assert [(e["state_id"], e["path"]) for e in error["errors"]] == [("s2", "doc")]
        assert "not guaranteed by the contracts" in error["errors"][0]["message"]
    else:
        assert "not guaranteed by the contracts" in str(exc_info.value)


def test_an_empty_entry_contract_with_a_missing_downstream_field_saves_when_the_linker_is_off():
    graph, report = _validate(_entry_workflow(EMPTY_CONTRACT), link_contracts=False)

    assert graph is not None
    assert report.status is ContractAnalysisStatus.NOT_RUN


def test_an_undeclared_entry_contract_keeps_the_initial_input_unknown_in_the_linker():
    report = link_workflow_contracts(_parsed(_entry_workflow(None)))

    record = _doc_record(report, "s2")
    assert (record.outcome.value, record.reported_as) == ("UNVERIFIED", ReportedAs.INFO)
    assert "initial_input" in record.provenance.sources_checked
    assert report.blocking_records() == []


def test_an_undeclared_entry_contract_is_accepted_by_the_enabled_pipeline_and_only_strict_rejects_it():
    graph, report = _validate(_entry_workflow(None), link_contracts=True)

    assert graph is not None
    assert _doc_record(report, "s2").outcome.value == "UNVERIFIED"
    assert report.blocking_records() == []

    with pytest.raises(ValueError):
        _validate({**_entry_workflow(None), "save_validation": "strict"}, link_contracts=True)


def test_a_clean_workflow_returns_the_graph_and_an_analysed_report():
    graph, report = _validate(_clean(), link_contracts=True)

    assert graph is not None
    assert report.status is ContractAnalysisStatus.ANALYSED
    assert report.blocking_records() == []


def test_the_linker_runs_after_the_graph_compiled():
    order: list[str] = []
    real_init = WorkflowExecutor._init_workflow

    def init(self):
        order.append("compile")
        return real_init(self)

    def link(config, resolver=None, document=None):
        order.append("link")
        return ContractLinkReport(status=ContractAnalysisStatus.ANALYSED)

    with (
        patch.object(WorkflowExecutor, "_init_workflow", init),
        patch("codemie.workflows.workflow.link_workflow_contracts", link),
    ):
        _validate(_clean(), link_contracts=True)

    assert order == ["compile", "link"]


def test_the_linker_receives_the_parsed_config_and_an_enabled_resolver():
    seen = {}

    def link(config, resolver=None, document=None):
        seen["config"] = config
        seen["resolver"] = resolver
        return ContractLinkReport(status=ContractAnalysisStatus.ANALYSED)

    stored = _stored(_clean())
    with patch("codemie.workflows.workflow.link_workflow_contracts", link):
        WorkflowExecutor.validate_workflow_with_report(stored, MagicMock(id="u1"), link_contracts=True)

    assert seen["config"] is stored
    assert isinstance(seen["resolver"], SaveTimeToolSchemaResolver)
    assert stored.states


# ---------------------------------------------------------------------------------------------------------------------
# validate_workflow / validate_workflow_and_draw
# ---------------------------------------------------------------------------------------------------------------------


def test_validate_workflow_returns_the_graph_element_of_the_report_call():
    sentinel_graph = object()
    with patch.object(
        WorkflowExecutor, "validate_workflow_with_report", return_value=(sentinel_graph, ContractLinkReport())
    ) as with_report:
        result = WorkflowExecutor.validate_workflow("config", "user", "json")

    assert result is sentinel_graph
    with_report.assert_called_once_with("config", "user", "json")


def test_validate_workflow_keeps_its_return_type_and_never_links():
    graph = WorkflowExecutor.validate_workflow(_stored(_clean()), MagicMock(id="u1"))
    assert hasattr(graph, "get_graph")

    WorkflowExecutor.validate_workflow(_stored(_removed_then_needed()), MagicMock(id="u1"))


# ---------------------------------------------------------------------------------------------------------------------
# Concurrency: reports are per call
# ---------------------------------------------------------------------------------------------------------------------


def test_two_concurrent_validations_keep_separate_reports():
    barrier = threading.Barrier(2, timeout=30)
    real_simulate = linker_module.simulate_workflow

    def simulate(*args, **kwargs):
        barrier.wait()  # both validations are inside the linker at the same moment
        return real_simulate(*args, **kwargs)

    def run(config_dict):
        return _validate(config_dict, link_contracts=True)[1]

    with patch.object(linker_module, "simulate_workflow", simulate):
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(run, _clean())
            second = pool.submit(run, _derived_error())
            clean_report, derived_report = first.result(timeout=60), second.result(timeout=60)

    assert clean_report is not derived_report
    assert clean_report.reachable_total == 2
    assert derived_report.reachable_total == 3
    assert {r.consumer_state_id for r in clean_report.records} <= {"s1", "s2"}
    assert "c1" in {r.consumer_state_id for r in derived_report.records}
    assert not any(r.outcome.value == "ERROR" for r in clean_report.records)


def test_many_concurrent_validations_never_leak_findings_between_calls():
    configs = [_removed_then_needed(), _clean()] * 6

    def run(config_dict):
        try:
            _validate(config_dict, link_contracts=True)
            return "saved"
        except ValueError:
            return "rejected"

    with ThreadPoolExecutor(max_workers=6) as pool:
        outcomes = list(pool.map(run, configs))

    assert outcomes == ["rejected", "saved"] * 6


# ---------------------------------------------------------------------------------------------------------------------
# build_contract_rejection
# ---------------------------------------------------------------------------------------------------------------------


def test_build_contract_rejection_is_none_without_blocking_records():
    assert build_contract_rejection(ContractLinkReport(), NullYamlLineFinder()) is None
    assert build_contract_rejection(link_workflow_contracts(_parsed(_clean())), NullYamlLineFinder()) is None


def test_build_contract_rejection_keeps_the_label_and_skips_the_state_id_for_graph_level_records():
    with patch.object(linker_module, "simulate_workflow", side_effect=RuntimeError("boom")):
        report = link_workflow_contracts(_parsed({**_clean(), "save_validation": "strict"}))

    json_error, string_error = build_contract_rejection(report, NullYamlLineFinder())

    assert json_error["error_type"] == WorkflowErrorType.CONTRACT_VALIDATION.value
    assert len(json_error["errors"]) == 1
    assert "state_id" not in json_error["errors"][0]
    assert json_error["errors"][0]["meta"] == {"label": CAUSE_SIMULATION_FAILED, "strict_source": "workflow"}
    assert json_error["coverage"]["analysed"] is False
    assert "<br>" in string_error and "boom" not in string_error


def test_the_linker_no_longer_shapes_errors():
    assert not hasattr(linker_module, "build_contract_error")
    assert not hasattr(linker_module, "_error_detail")
    assert not hasattr(linker_module, "_error_line")


# ---------------------------------------------------------------------------------------------------------------------
# Import direction
# ---------------------------------------------------------------------------------------------------------------------

FORBIDDEN_PREFIXES = (
    "codemie.workflows.nodes",
    "codemie.workflows.validation",
    "codemie.workflows.workflow",
    "codemie.service",
    "codemie.agents",
    "codemie.rest_api",
    "codemie.configs",
)


@pytest.mark.parametrize(
    "statements",
    [
        "import codemie.core.workflow_models; import codemie.workflows.validation",
        "import codemie.workflows.validation; import codemie.core.workflow_models",
        "import codemie.workflows.validation",
        "import codemie.core.workflow_models",
        "from codemie.workflows.validation import link_workflow_contracts",
    ],
)
def test_fresh_interpreter_imports_succeed_in_either_order(statements):
    completed = subprocess.run(
        [sys.executable, "-I", "-c", statements], capture_output=True, text=True, timeout=180, check=False
    )
    assert completed.returncode == 0, completed.stderr[-2000:]


def test_fresh_interpreter_importing_the_linker_loads_no_runtime_module():
    code = (
        "import sys\n"
        "import codemie.workflows.contracts.linker\n"
        f"bad = sorted(m for m in sys.modules if m.startswith({FORBIDDEN_PREFIXES!r}))\n"
        "assert not bad, bad\n"
    )
    completed = subprocess.run(
        [sys.executable, "-I", "-c", code], capture_output=True, text=True, timeout=180, check=False
    )
    assert completed.returncode == 0, completed.stderr[-2000:]


def _runtime_codemie_imports(path: Path) -> set[str]:
    """Imported `codemie.*` modules of a file, leaving out the `if TYPE_CHECKING:` blocks."""
    tree = ast.parse(path.read_text())
    guarded = {
        id(child)
        for node in ast.walk(tree)
        if isinstance(node, ast.If) and "TYPE_CHECKING" in ast.unparse(node.test)
        for child in ast.walk(node)
    }
    modules: set[str] = set()
    for node in ast.walk(tree):
        if id(node) in guarded:
            continue
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("codemie"):
            modules.add(node.module)
        elif isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names if alias.name.startswith("codemie"))
    return modules


@pytest.mark.parametrize("path", sorted(CONTRACTS_DIR.glob("*.py")), ids=lambda p: p.name)
def test_contracts_modules_import_only_leaf_modules(path):
    allowed = (
        "codemie.workflows.constants",
        "codemie.workflows.contracts",
        "codemie.workflows.transform_constants",
        "codemie.workflows.context_rules",
    )
    offenders = [m for m in _runtime_codemie_imports(path) if not m.startswith(allowed)]
    assert offenders == []
