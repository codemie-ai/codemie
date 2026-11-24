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

"""Contract models and report (EPMCDME-16503, T3): record kinds, outcome vs reported_as, coverage data, JSON dump."""

import json
from typing import get_args

import pytest
from pydantic import ValidationError

from codemie.workflows.constants import ContractValidationMode
from codemie.workflows.contracts.models import (
    ContractAnalysisStatus,
    ContractLinkReport,
    ContractRecord,
    ContractRequirement,
    Outcome,
    Provenance,
    RecordKind,
    ReportedAs,
    RequirementKind,
)


def test_record_kinds_are_the_fixed_set():
    assert {kind.value for kind in RecordKind} == {
        "declared",
        "derived",
        "coverage",
        "contract-form",
        "convergence",
        "analysis-failure",
    }


def test_outcomes_and_reported_as_are_separate_vocabularies():
    assert {o.value for o in Outcome} == {"OK", "ERROR", "WARNING", "UNVERIFIED"}
    assert {r.value for r in ReportedAs} == {"blocks", "warning", "info"}


def test_requirement_kinds_are_declared_and_derived_only():
    assert get_args(RequirementKind) == (RecordKind.DECLARED, RecordKind.DERIVED)
    with pytest.raises(ValidationError):
        ContractRequirement(state_id="s1", path="p", name="n", kind=RecordKind.COVERAGE)


def test_requirement_defaults_to_required_with_unknown_types():
    requirement = ContractRequirement(
        state_id="s1", path="input_schema.doc", name="doc", kind=RecordKind.DECLARED, origin="input_schema"
    )
    assert requirement.required is True
    assert requirement.types is None


def test_record_keeps_raw_outcome_apart_from_reported_as_and_starts_unclassified():
    record = ContractRecord(
        kind=RecordKind.DERIVED,
        outcome=Outcome.ERROR,
        consumer_state_id="s2",
        path="task",
        message="Field 'x' is not guaranteed by the contracts",
    )
    assert record.outcome is Outcome.ERROR
    assert record.reported_as is None
    record.reported_as = ReportedAs.WARNING
    assert record.outcome is Outcome.ERROR
    assert record.reported_as is ReportedAs.WARNING


def test_provenance_defaults_are_empty():
    provenance = Provenance()
    assert provenance.producers == ()
    assert provenance.sources_checked == ()
    assert provenance.overwritten_by is None
    assert provenance.removed_by is None
    assert provenance.mechanism is None


def _record(
    reported_as: ReportedAs | None = ReportedAs.INFO,
    outcome: Outcome = Outcome.ERROR,
    kind: RecordKind = RecordKind.DECLARED,
    state_id: str = "a",
) -> ContractRecord:
    return ContractRecord(
        kind=kind, outcome=outcome, reported_as=reported_as, consumer_state_id=state_id, path="input_schema.x"
    )


def _graph_failure() -> ContractRecord:
    return _record(ReportedAs.INFO, Outcome.UNVERIFIED, RecordKind.ANALYSIS_FAILURE, state_id="")


def test_new_report_is_empty_and_was_not_run():
    report = ContractLinkReport()
    assert report.status is ContractAnalysisStatus.NOT_RUN
    assert report.records == ()
    assert report.cap_reached is False
    assert report.validated_count == 0
    assert report.reachable_total == 0
    assert report.uncovered_state_ids == ()
    assert report.unreachable_state_ids == ()
    assert report.effective_modes == {}


def test_the_three_statuses_are_the_fixed_set():
    assert {status.value for status in ContractAnalysisStatus} == {"not_run", "failed", "analysed"}


def test_there_is_no_analysed_field_any_more():
    assert "analysed" not in ContractLinkReport.model_fields
    with pytest.raises(ValidationError):
        ContractLinkReport(analysed=True)


def test_a_report_is_immutable():
    report = ContractLinkReport(status=ContractAnalysisStatus.ANALYSED, records=(_record(),))

    with pytest.raises(ValidationError):
        report.status = ContractAnalysisStatus.NOT_RUN
    with pytest.raises(ValidationError):
        report.records = ()
    with pytest.raises(AttributeError):
        report.records.append(_record())  # a tuple


def test_a_report_takes_its_collections_as_tuples_even_when_given_lists():
    report = ContractLinkReport(
        status=ContractAnalysisStatus.ANALYSED,
        records=[_record()],
        uncovered_state_ids=["c"],
        unreachable_state_ids=["z"],
    )

    assert isinstance(report.records, tuple)
    assert report.uncovered_state_ids == ("c",)
    assert report.unreachable_state_ids == ("z",)


def test_reports_do_not_share_state():
    first, second = ContractLinkReport(), ContractLinkReport()
    first.effective_modes["s1"] = ContractValidationMode.STRICT
    assert second.effective_modes == {}


def test_a_record_the_policy_has_not_classified_is_rejected():
    with pytest.raises(ValidationError, match="reported_as"):
        ContractLinkReport(status=ContractAnalysisStatus.ANALYSED, records=(_record(reported_as=None),))


def test_a_record_that_blocks_cannot_be_an_ok_one():
    with pytest.raises(ValidationError, match="cannot block"):
        ContractLinkReport(
            status=ContractAnalysisStatus.ANALYSED,
            records=(_record(ReportedAs.BLOCKS, Outcome.OK),),
        )


@pytest.mark.parametrize("outcome", [Outcome.ERROR, Outcome.WARNING, Outcome.UNVERIFIED])
def test_a_record_that_blocks_may_carry_any_other_outcome(outcome):
    report = ContractLinkReport(status=ContractAnalysisStatus.ANALYSED, records=(_record(ReportedAs.BLOCKS, outcome),))

    assert len(report.blocking_records()) == 1


def test_a_report_that_was_not_run_cannot_carry_records():
    with pytest.raises(ValidationError, match="not run"):
        ContractLinkReport(records=(_record(),))


def test_a_failed_report_needs_the_graph_level_failure_record():
    assert len(ContractLinkReport(status=ContractAnalysisStatus.FAILED, records=(_graph_failure(),)).records) == 1
    with pytest.raises(ValidationError, match="failed report"):
        ContractLinkReport(status=ContractAnalysisStatus.FAILED)
    with pytest.raises(ValidationError, match="failed report"):
        ContractLinkReport(status=ContractAnalysisStatus.FAILED, records=(_record(),))
    # a failure of one state is not the graph-level failure
    state_failure = _record(ReportedAs.INFO, Outcome.UNVERIFIED, RecordKind.ANALYSIS_FAILURE, state_id="s1")
    with pytest.raises(ValidationError, match="failed report"):
        ContractLinkReport(status=ContractAnalysisStatus.FAILED, records=(state_failure,))


def test_blocking_records_are_selected_by_reported_as_not_by_outcome():
    blocks = _record(ReportedAs.BLOCKS, Outcome.ERROR, state_id="a")
    warned_error = _record(ReportedAs.WARNING, Outcome.ERROR, RecordKind.DERIVED, state_id="b")
    info_ok = _record(ReportedAs.INFO, Outcome.OK, RecordKind.DERIVED, state_id="c")

    report = ContractLinkReport(status=ContractAnalysisStatus.ANALYSED, records=(blocks, warned_error, info_ok))

    assert report.blocking_records() == [blocks]


def test_json_dump_is_plain_json_with_enum_values_and_coverage_data():
    report = ContractLinkReport(
        status=ContractAnalysisStatus.ANALYSED,
        cap_reached=True,
        validated_count=2,
        reachable_total=3,
        uncovered_state_ids=["c"],
        unreachable_state_ids=["z"],
        effective_modes={"a": ContractValidationMode.WARN},
        records=[
            ContractRecord(
                kind=RecordKind.CONTRACT_FORM,
                outcome=Outcome.WARNING,
                reported_as=ReportedAs.INFO,
                consumer_state_id="a",
                path="output_schema.doc",
                field="doc",
                message="m",
                provenance=Provenance(
                    producers=("p1", "p2"), sources_checked=("p1",), removed_by="r", mechanism="reset"
                ),
            )
        ],
    )
    dumped = json.loads(report.to_json())
    assert dumped == report.to_dict()
    assert dumped["status"] == "analysed"
    assert "analysed" not in dumped
    assert dumped["cap_reached"] is True
    assert dumped["validated_count"] == 2
    assert dumped["reachable_total"] == 3
    assert dumped["uncovered_state_ids"] == ["c"]
    assert dumped["unreachable_state_ids"] == ["z"]
    assert dumped["effective_modes"] == {"a": "warn"}
    record = dumped["records"][0]
    assert record["kind"] == "contract-form"
    assert record["outcome"] == "WARNING"
    assert record["reported_as"] == "info"
    assert (record["path"], record["field"]) == ("output_schema.doc", "doc")
    assert record["provenance"]["producers"] == ["p1", "p2"]
    assert record["provenance"]["mechanism"] == "reset"


def test_report_to_dict_snapshot():
    """The shape the API layer builds on: a rename or a removed key must be a deliberate change to this test."""
    report = ContractLinkReport(
        status=ContractAnalysisStatus.ANALYSED,
        records=(
            ContractRecord(
                kind=RecordKind.DECLARED,
                outcome=Outcome.ERROR,
                reported_as=ReportedAs.BLOCKS,
                consumer_state_id="b",
                path="input_schema.doc",
                message="m",
                label="not_guaranteed",
                provenance=Provenance(
                    producers=("a",),
                    sources_checked=("a", "initial_input"),
                    overwritten_by="c",
                    removed_by="d",
                    mechanism="reset",
                ),
            ),
        ),
        cap_reached=False,
        validated_count=1,
        reachable_total=2,
        uncovered_state_ids=("a",),
        unreachable_state_ids=("z",),
        effective_modes={"a": ContractValidationMode.WARN, "b": ContractValidationMode.STRICT},
    )

    assert report.to_dict() == {
        "status": "analysed",
        "records": [
            {
                "kind": "declared",
                "outcome": "ERROR",
                "reported_as": "blocks",
                "consumer_state_id": "b",
                "path": "input_schema.doc",
                "location": None,
                "field": None,
                "message": "m",
                "label": "not_guaranteed",
                "strict_source": None,
                "provenance": {
                    "producers": ["a"],
                    "sources_checked": ["a", "initial_input"],
                    "overwritten_by": "c",
                    "removed_by": "d",
                    "mechanism": "reset",
                },
            }
        ],
        "cap_reached": False,
        "validated_count": 1,
        "reachable_total": 2,
        "uncovered_state_ids": ["a"],
        "unreachable_state_ids": ["z"],
        "effective_modes": {"a": "warn", "b": "strict"},
    }
    assert ContractLinkReport().to_dict() == {
        "status": "not_run",
        "records": [],
        "cap_reached": False,
        "validated_count": 0,
        "reachable_total": 0,
        "uncovered_state_ids": [],
        "unreachable_state_ids": [],
        "effective_modes": {},
    }


@pytest.mark.parametrize("bad_kind", ["bogus", ""])
def test_record_rejects_unknown_kind(bad_kind):
    with pytest.raises(ValueError):
        ContractRecord(kind=bad_kind, outcome=Outcome.OK, consumer_state_id="a", path="p")
