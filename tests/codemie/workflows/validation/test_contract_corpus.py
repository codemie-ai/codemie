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

"""Built-in template corpus run of the contract linker (EPMCDME-16503, T9).

AC-22: no template gains a rejection (no record whose `reported_as` blocks). AC-43 / D2: no raw `outcome == ERROR` on
any template (no known-exceptions list) and the warnings, counted as records `reported_as` warning, stay within a
ceiling checked in as `contract_corpus_warning_ceiling.json`, per template and in total. The loader reads
`config/templates/workflow/*_template.yaml` through `WorkflowConfigTemplate.from_yaml` and calls the linker directly with
no resolver, so tool bindings are UNVERIFIED and missing tool schemas are tolerated.

The EPMCDME-16499 harness is a required dependency. Its loader is shared by the direct linker corpus and the save
path checks. The AC-40 reducer checks live in `test_contract_effects.py`.

To re-record the ceiling after a deliberate template or linker change, dump
`{"per_template": measured_warnings(link_corpus(load_corpus())), "total": <sum>}` from this module into the JSON file.
"""

import importlib.util
import json
import time
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import yaml

from codemie.core.workflow_models.workflow_config import WorkflowConfig, WorkflowConfigTemplate
from codemie.core.workflow_models.workflow_models import WorkflowState
from codemie.workflows.contracts.linker import link_workflow_contracts
from codemie.workflows.contracts.models import ContractAnalysisStatus, ContractLinkReport, Outcome, ReportedAs
from codemie.workflows.workflow import WorkflowExecutor

REPO_ROOT = Path(__file__).resolve().parents[4]
TEMPLATES_DIR = REPO_ROOT / "config" / "templates" / "workflow"
CEILING_FILE = Path(__file__).resolve().parent / "contract_corpus_warning_ceiling.json"
HARNESS_FILE = REPO_ROOT / "tests" / "codemie" / "workflows" / "test_workflow_templates_validation.py"

MIN_TEMPLATE_COUNT = 64
UNSTRUCTURED_TEMPLATE = "test_cases_from_unstructured_data_template.yaml"
ITERATION_KEY_PATH = "next.iter_key"

# --- loader -----------------------------------------------------------------------------------------


def load_corpus() -> dict[str, WorkflowConfigTemplate]:
    """Every built-in template, parsed; fails on a short corpus, an unloadable file or a non-`WorkflowState` state."""
    paths = sorted(TEMPLATES_DIR.glob("*_template.yaml"))
    assert (
        len(paths) >= MIN_TEMPLATE_COUNT
    ), f"only {len(paths)} templates found, expected at least {MIN_TEMPLATE_COUNT}"
    corpus: dict[str, WorkflowConfigTemplate] = {}
    harness = _import_harness()
    for path in paths:
        template = harness._load_template(path)
        assert template is not None, f"{path.name} failed to load"
        template.parse_execution_config()
        foreign = [type(state).__name__ for state in template.states if not isinstance(state, WorkflowState)]
        assert (
            not foreign
        ), f"{path.name} has non-WorkflowState states (from_yaml fell back to model_construct): {foreign}"
        corpus[path.name] = template
    return corpus


def link_corpus(corpus: dict[str, WorkflowConfigTemplate]) -> dict[str, ContractLinkReport]:
    return {name: link_workflow_contracts(template, resolver=None) for name, template in corpus.items()}


def count_warnings(report: ContractLinkReport) -> int:
    return sum(1 for record in report.records if record.reported_as is ReportedAs.WARNING)


def measured_warnings(reports: dict[str, ContractLinkReport]) -> dict[str, int]:
    return {name: count_warnings(report) for name, report in reports.items()}


# --- ceiling ----------------------------------------------------------------------------------------


def load_ceiling() -> dict:
    return json.loads(CEILING_FILE.read_text(encoding="utf-8"))


def ceiling_violations(measured: dict[str, int], ceiling: dict) -> list[str]:
    """Human-readable breaches: a template above its recorded ceiling (unlisted means 0) or the total above its own."""
    per_template = ceiling["per_template"]
    problems = [
        f"{name}: {count} warnings, ceiling {per_template.get(name, 0)}"
        for name, count in sorted(measured.items())
        if count > per_template.get(name, 0)
    ]
    total = sum(measured.values())
    if total > ceiling["total"]:
        problems.append(f"total: {total} warnings, ceiling {ceiling['total']}")
    return problems


@pytest.fixture(scope="module")
def corpus() -> dict[str, WorkflowConfigTemplate]:
    return load_corpus()


@pytest.fixture(scope="module")
def reports(corpus) -> dict[str, ContractLinkReport]:
    return link_corpus(corpus)


# --- loader guards ----------------------------------------------------------------------------------


def test_the_loader_finds_the_whole_corpus(corpus):
    assert len(corpus) >= MIN_TEMPLATE_COUNT
    assert all(isinstance(state, WorkflowState) for template in corpus.values() for state in template.states)


def test_the_loader_fails_on_a_short_corpus(tmp_path):
    (tmp_path / "only_one_template.yaml").write_text("name: x\n")
    with patch(f"{__name__}.TEMPLATES_DIR", tmp_path), pytest.raises(AssertionError, match="expected at least 64"):
        load_corpus()


def test_the_loader_fails_on_a_non_workflow_state_state():
    # what the model_construct fallback of from_yaml can leave behind
    fallback = SimpleNamespace(states=[object()], parse_execution_config=lambda: None)
    with (
        patch.object(WorkflowConfigTemplate, "from_yaml", return_value=fallback),
        pytest.raises(AssertionError, match="non-WorkflowState"),
    ):
        load_corpus()


# --- AC-22 / AC-43: the corpus ------------------------------------------------------------------------


def test_ac43_no_template_has_a_raw_error_outcome(reports):
    offenders = {
        name: [(r.consumer_state_id, r.path) for r in report.records if r.outcome is Outcome.ERROR]
        for name, report in reports.items()
        if any(r.outcome is Outcome.ERROR for r in report.records)
    }
    assert offenders == {}


def test_ac22_no_template_has_a_blocking_record(reports):
    offenders = {name: len(report.blocking_records()) for name, report in reports.items() if report.blocking_records()}
    assert offenders == {}
    assert all(record.reported_as is not None for report in reports.values() for record in report.records)


def test_every_template_is_analysed_and_none_reaches_the_work_cap(reports):
    assert [name for name, report in reports.items() if report.status is not ContractAnalysisStatus.ANALYSED] == []
    assert [name for name, report in reports.items() if report.cap_reached] == []


def test_the_unstructured_data_template_has_no_iteration_key_record(reports):
    # Both states iterate over a list-form output; the runtime iterates it whole and ignores the key.
    records = reports[UNSTRUCTURED_TEMPLATE].records
    assert [r for r in records if r.path == ITERATION_KEY_PATH or ITERATION_KEY_PATH in r.path] == []
    assert any(r.reported_as is ReportedAs.WARNING for r in records)


def test_ac43_warnings_stay_within_the_recorded_ceiling(reports):
    assert ceiling_violations(measured_warnings(reports), load_ceiling()) == []


def test_ac43_the_ceiling_file_is_consistent_and_names_every_template(corpus):
    ceiling = load_ceiling()
    per_template = ceiling["per_template"]
    assert set(per_template) == set(corpus)
    assert ceiling["total"] == sum(per_template.values())
    assert all(isinstance(value, int) and value >= 0 for value in per_template.values())


def test_the_ceiling_check_reports_a_template_and_a_total_breach():
    ceiling = {"per_template": {"a": 1, "b": 0}, "total": 1}

    assert ceiling_violations({"a": 1, "b": 0}, ceiling) == []
    assert ceiling_violations({"a": 0, "b": 0}, ceiling) == []
    assert ceiling_violations({"a": 2, "b": 0}, ceiling) == [
        "a: 2 warnings, ceiling 1",
        "total: 2 warnings, ceiling 1",
    ]
    assert ceiling_violations({"a": 1, "b": 1}, ceiling)[0] == "b: 1 warnings, ceiling 0"
    assert ceiling_violations({"a": 0, "b": 0, "c": 1}, ceiling)[0] == "c: 1 warnings, ceiling 0"


# --- timing: the linker against the current save path -------------------------------------------------

TIMING_RUNS = 5
MAX_LINKER_TO_VALIDATE_RATIO = 5
MAX_LINKER_SECONDS = 2.0


def _best_of(call) -> float:
    """The fastest of a few runs: the least disturbed by whatever else the parallel test workers are doing."""
    best = float("inf")
    for _ in range(TIMING_RUNS):
        started = time.perf_counter()
        call()
        best = min(best, time.perf_counter() - started)
    return best


def _diamond_chain_yaml(diamonds: int = 20, total: int = 200) -> str:
    states = []
    index = 0
    for _ in range(diamonds):
        states.append(
            {
                "id": f"s{index}",
                "assistant_id": "a1",
                "next": {"condition": {"expression": "True", "then": f"s{index + 1}", "otherwise": f"s{index + 2}"}},
            }
        )
        states.append({"id": f"s{index + 1}", "assistant_id": "a1", "next": {"state_id": f"s{index + 2}"}})
        index += 2
    while index < total:
        states.append({"id": f"s{index}", "assistant_id": "a1", "next": {"state_id": f"s{index + 1}"}})
        index += 1
    states.append({"id": f"s{total}", "assistant_id": "a1", "next": {"state_id": "end"}})
    return yaml.safe_dump({"assistants": [{"id": "a1", "system_prompt": "do it"}], "states": states})


def _stored_workflow(yaml_config: str, mode: str = "Sequential") -> WorkflowConfig:
    return WorkflowConfig(
        id="wf-timing", name="wf", description="d", project="demo", mode=mode, yaml_config=yaml_config
    )


def _assert_linker_within_limits(workflow: WorkflowConfig) -> None:
    parsed = workflow.model_copy()
    parsed.parse_execution_config()
    with _stage_doubles():
        baseline = _best_of(
            lambda: WorkflowExecutor.validate_workflow_with_report(
                workflow, MagicMock(id="u1"), error_format="string", link_contracts=False
            )
        )
    linker = _best_of(lambda: link_workflow_contracts(parsed, resolver=None))

    assert linker < MAX_LINKER_SECONDS
    assert linker <= MAX_LINKER_TO_VALIDATE_RATIO * baseline, f"linker {linker:.4f}s, validate_workflow {baseline:.4f}s"


def test_the_linker_on_a_two_hundred_state_chain_with_twenty_diamonds_is_within_five_times_validate_workflow():
    _assert_linker_within_limits(_stored_workflow(_diamond_chain_yaml()))


def test_the_linker_on_the_largest_template_is_within_five_times_validate_workflow(corpus):
    template = max(corpus.values(), key=lambda item: len(item.states))

    _assert_linker_within_limits(_stored_workflow(template.yaml_config, mode=template.mode))


# --- required EPMCDME-16499 harness --------------------------------------------------------------------


def _import_harness():
    spec = importlib.util.spec_from_file_location("contract_corpus_16499_harness", HARNESS_FILE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@contextmanager
def _stage_doubles():
    """The save path with the resource stage and the executor compile replaced, so only the linker is under test."""
    with (
        patch("codemie.workflows.workflow.validate_workflow_config_resources_availability"),
        patch.object(
            WorkflowExecutor,
            "create_executor",
            MagicMock(return_value=MagicMock(_init_workflow=MagicMock(return_value=None))),
        ),
    ):
        yield


def _harness_validate_with_linker(harness, path: Path) -> ContractLinkReport:
    """The harness's own save path for one template (resources and executor patched), with the linker on."""
    template = harness._load_template(path)
    assert template is not None, f"{path.name} failed to load"
    workflow_config = WorkflowConfig(
        name=template.name, description=template.description, mode=template.mode, yaml_config=template.yaml_config
    )
    user = MagicMock()
    user.project_names = ["app"]
    with _stage_doubles():
        _, report = WorkflowExecutor.validate_workflow_with_report(
            workflow_config, user, error_format="string", link_contracts=True
        )
    return report


def test_the_16499_harness_and_this_loader_see_the_same_corpus(corpus):
    harness = _import_harness()

    assert len(harness.TEMPLATE_FILES) >= harness.MIN_TEMPLATE_COUNT
    assert {path.name for path in harness.TEMPLATE_FILES} == set(corpus)


def test_ac22_ac43_every_template_saves_through_the_harness_path_with_the_linker_on(corpus):
    harness = _import_harness()
    rejected: dict[str, str] = {}
    measured: dict[str, int] = {}
    errors: dict[str, int] = {}
    for path in harness.TEMPLATE_FILES:
        try:
            report = _harness_validate_with_linker(harness, path)
        except ValueError as error:
            rejected[path.name] = str(error)[:200]
            continue
        measured[path.name] = count_warnings(report)
        errors[path.name] = sum(1 for record in report.records if record.outcome is Outcome.ERROR)

    assert rejected == {}
    assert {name: count for name, count in errors.items() if count} == {}
    assert ceiling_violations(measured, load_ceiling()) == []


def test_ac40_full_reset_with_an_output_key_saves_with_only_the_key_available():
    states = [
        {
            "id": "seed",
            "assistant_id": "a1",
            "output_schema": '{"type":"object","properties":{"seed":{"type":"string"}},"required":["seed"]}',
            "next": {"state_id": "collapse"},
        },
        {
            "id": "collapse",
            "assistant_id": "a1",
            "next": {"state_id": "use", "clear_context_store": True, "output_key": "result"},
        },
        {
            "id": "use",
            "assistant_id": "a1",
            "input_schema": '{"type":"object","properties":{"result":{"type":"string"}},"required":["result"]}',
            "next": {"state_id": "end"},
        },
    ]
    stored = WorkflowConfig(
        id="wf-ac40",
        name="wf",
        description="d",
        project="demo",
        mode="Sequential",
        yaml_config=yaml.safe_dump({"assistants": [{"id": "a1", "system_prompt": "do it"}], "states": states}),
    )

    with _stage_doubles():
        _, report = WorkflowExecutor.validate_workflow_with_report(
            stored, MagicMock(id="u1"), error_format="string", link_contracts=True
        )

    assert report.status is ContractAnalysisStatus.ANALYSED
    assert report.blocking_records() == []
    assert [r for r in report.records if r.outcome in (Outcome.ERROR, Outcome.WARNING)] == []
    assert [r.outcome for r in report.records if r.consumer_state_id == "use" and r.path == "input_schema.result"] == [
        Outcome.OK
    ]
