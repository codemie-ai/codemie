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

"""Shapes a `ContractLinkReport` into API issues (EPMCDME-16504): errors, capped warnings and coverage.

Errors are the records the policy blocks on (`reported_as == blocks`). Warnings are the non-blocking records whose raw
outcome is not OK, so a warn-mode UNVERIFIED record (reported as `info`) still shows. `outcome` of an entry is mapped
from `reported_as`, never taken from the raw outcome. Entries are `WorkflowValidationErrorDetail` dumps.

`config_path` is formatted from the typed source location. Line lookup uses its exact tokens independently of
the public display path. An absent location never triggers ownership guesses from a diagnostic string.

Nothing here may change whether a workflow is rejected: the blocking set is decided before any record is shaped, each
record is shaped in its own try, and a record that cannot be shaped degrades to a minimal entry. Imports only
contract policy/models and validation helpers; `contracts/linker.py` does not import this module.
"""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING, Any, NamedTuple


from codemie.workflows.constants import WorkflowErrorType
from codemie.workflows.contracts.models import (
    ContractAnalysisStatus,
    ContractLinkReport,
    ContractRecord,
    ContractLocation,
    LocationSection,
    Outcome,
    ReportedAs,
)
from codemie.workflows.contracts.policy import raw_name
from codemie.workflows.validation.line_lookup import NullYamlLineFinder, ValidationSourceContext
from codemie.workflows.validation.models import ContractMeta, WorkflowValidationErrorDetail

if TYPE_CHECKING:
    from codemie.workflows.validation.line_lookup import YamlLineFinderBase

logger = logging.getLogger(__name__)

CONTRACT_WARNINGS_CAP = 100
CONTRACT_ERROR_MESSAGE = "Workflow contracts are not satisfied"

_ID_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "codemie.workflows.validation.contract_issues")

_OUTCOME_OF = {
    ReportedAs.BLOCKS: "ERROR",
    ReportedAs.WARNING: "WARNING",
    ReportedAs.INFO: "UNVERIFIED",
}


class ContractIssues(NamedTuple):
    errors: list[dict[str, Any]]
    warnings: list[dict[str, Any]]
    coverage: dict[str, Any]
    warnings_truncated: int


def format_config_path(location: ContractLocation | None) -> str | None:
    """Format typed segments using the established dotted public path syntax."""
    if location is None or not location.segments:
        return None
    return raw_name(".".join(str(segment) for segment in location.segments))


def build_contract_issues(report: ContractLinkReport, line_finder: YamlLineFinderBase) -> ContractIssues:
    """The errors, capped warnings and coverage of one report. Never raises on a malformed record."""
    blocking = [record for record in report.records if record.reported_as is ReportedAs.BLOCKS]
    advisory = [
        record
        for record in report.records
        if record.reported_as is not ReportedAs.BLOCKS and record.outcome is not Outcome.OK
    ]
    errors = [
        _shape(record, line_finder, index=index, fallback_outcome="ERROR") for index, record in enumerate(blocking)
    ]
    warnings = [
        _shape(
            record,
            line_finder,
            kind=WorkflowErrorType.CONTRACT_VALIDATION.value,
            index=index,
            fallback_outcome=_outcome(record),
        )
        for index, record in enumerate(advisory)
    ]
    kept = warnings[:CONTRACT_WARNINGS_CAP]
    return ContractIssues(errors, kept, _coverage(report), len(warnings) - len(kept))


def build_contract_rejection(
    report: ContractLinkReport, line_finder: YamlLineFinderBase
) -> tuple[dict[str, Any], str] | None:
    """The `(json_error, string_error)` pair of a report, or None when no record blocks.

    The json form is the executor's raised dict: the envelope, the errors, the capped warnings and the coverage, plus
    `warnings_truncated` only when the cap cut some. The string form is `<br>`-joined: error lines, warning lines
    prefixed by outcome, the `warnings_truncated: <N>` line when N > 0, then one coverage line. Messages come from the
    policy's fixed templates (names sanitised there); the coverage line carries counts only.
    """
    if not report.blocking_records():
        return None
    issues = build_contract_issues(report, line_finder)
    json_error: dict[str, Any] = {
        "error_type": WorkflowErrorType.CONTRACT_VALIDATION.value,
        "message": CONTRACT_ERROR_MESSAGE,
        "errors": issues.errors,
        "warnings": issues.warnings,
        "coverage": issues.coverage,
    }
    if issues.warnings_truncated:
        json_error["warnings_truncated"] = issues.warnings_truncated
    return json_error, _rejection_text(issues)


def _rejection_text(issues: ContractIssues) -> str:
    lines = [_error_line(entry) for entry in issues.errors]
    lines += [f"{entry.get('outcome', 'UNVERIFIED')}: {_error_line(entry)}" for entry in issues.warnings]
    if issues.warnings_truncated:
        lines.append(f"warnings_truncated: {issues.warnings_truncated}")
    lines.append(_coverage_line(issues.coverage))
    body = "\n".join(lines)
    return f"\n{CONTRACT_ERROR_MESSAGE}: \n{body}\n".replace("\n", "<br>")


def _error_line(entry: dict[str, Any]) -> str:
    label = (entry.get("meta") or {}).get("label")
    return f"{entry['message']} ({label})" if label else str(entry["message"])


def _coverage_line(coverage: dict[str, Any]) -> str:
    checked, total = coverage["validated_states"], coverage["total_states"]
    return f"Contracts checked on {checked} of {total} reachable states"


def _shape(
    record: ContractRecord,
    line_finder: YamlLineFinderBase,
    kind: str | None = None,
    *,
    index: int,
    fallback_outcome: str,
) -> dict[str, Any]:
    try:
        entry = _detail(record, line_finder).model_dump(exclude_none=True)
    except Exception:
        logger.warning("Contract issue could not be shaped, using the minimal entry", exc_info=True)
        try:
            entry = WorkflowValidationErrorDetail(**_minimal(record)).model_dump(exclude_none=True)
        except Exception:
            logger.warning("Contract issue minimal formatting failed; using a fixed entry")
            entry = {
                "id": f"contract-fallback-{fallback_outcome}-{index}",
                "path": "",
                "outcome": fallback_outcome,
                "message": "Workflow contract issue details are unavailable",
            }
    if kind:
        entry["kind"] = kind
    return entry


def _detail(record: ContractRecord, line_finder: YamlLineFinderBase) -> WorkflowValidationErrorDetail:
    location = record.location
    config_path = format_config_path(location)
    return WorkflowValidationErrorDetail(
        id=_issue_id(record),
        message=record.message,
        path=record.field or "",
        state_id=record.consumer_state_id or None,
        config_path=config_path,
        config_section=location.section.value
        if location and location.section is not LocationSection.WORKFLOW
        else None,
        config_line=_optional(line_finder.find_contract_line, location) if location else None,
        outcome=_outcome(record),
        details=_optional(_provenance_line, record),
        meta=_meta(record),
    )


def _meta(record: ContractRecord) -> ContractMeta | None:
    if not record.label and record.strict_source is None:
        return None
    return ContractMeta(label=record.label or None, strict_source=record.strict_source)


def _minimal(record: ContractRecord) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "id": _issue_id(record),
        "path": record.field or "",
        "outcome": _outcome(record),
        "message": record.message,
    }
    if record.consumer_state_id:
        entry["state_id"] = record.consumer_state_id
    if record.strict_source is not None:
        entry["meta"] = {"strict_source": record.strict_source.value}
    return entry


def _optional(build: Any, *args: Any) -> Any:
    """One optional key: a failure is logged and drops only that key."""
    try:
        return build(*args)
    except Exception:
        logger.warning("Contract issue key could not be built, omitting it", exc_info=True)
        return None


def _outcome(record: ContractRecord) -> str:
    return _OUTCOME_OF.get(record.reported_as, "UNVERIFIED")


def _issue_id(record: ContractRecord) -> str:
    producers = getattr(record.provenance, "producers", ())
    seed = f"{record.consumer_state_id}|{record.path}|{record.kind.value}|{record.message}|{record.field}|{producers}"
    return str(uuid.uuid5(_ID_NAMESPACE, seed))


def contract_line_finder(
    workflow_config: Any, source_context: ValidationSourceContext | None = None
) -> YamlLineFinderBase:
    """Lookup against submitted YAML, with request-local reuse and a null finder on invalid source."""
    try:
        text = getattr(workflow_config, "_source_yaml_config", None) or workflow_config.yaml_config
        if not isinstance(text, str):
            return NullYamlLineFinder()
        return (source_context or ValidationSourceContext()).source(text).finder
    except Exception:
        logger.warning("Contract line finder could not be built, lines are omitted", exc_info=True)
        return NullYamlLineFinder()


def _provenance_line(record: ContractRecord) -> str | None:
    provenance = record.provenance
    parts: list[str] = []
    if provenance.producers:
        parts.append("producers: " + ", ".join(provenance.producers))
    if provenance.removed_by:
        removed = f"removed by: {provenance.removed_by}"
        parts.append(f"{removed} ({provenance.mechanism})" if provenance.mechanism else removed)
    if provenance.overwritten_by:
        parts.append(f"overwritten by: {provenance.overwritten_by}")
    if provenance.sources_checked:
        parts.append("sources checked: " + ", ".join(provenance.sources_checked))
    return "; ".join(parts) or None


def _coverage(report: ContractLinkReport) -> dict[str, Any]:
    try:
        return {
            "validated_states": report.validated_count,
            "total_states": report.reachable_total,
            "uncovered_state_ids": list(report.uncovered_state_ids),
            "analysed": report.status is ContractAnalysisStatus.ANALYSED,
            "cap_reached": report.cap_reached,
        }
    except Exception:
        logger.warning("Contract coverage could not be built", exc_info=True)
        return {
            "validated_states": 0,
            "total_states": 0,
            "uncovered_state_ids": [],
            "analysed": False,
            "cap_reached": False,
        }
