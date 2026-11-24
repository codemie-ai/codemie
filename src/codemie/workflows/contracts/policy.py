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

"""Mode policy, coverage, failure isolation and messages (EPMCDME-16503, AC-17..AC-22).

`build_report(result, states=..., settings=...)` turns one `SimulationResult` into the finished `ContractLinkReport`:

* every record gets `reported_as` from the effective mode of its consumer state (state `save_validation` overrides the
  workflow mode, AC-20) and a message rendered from a fixed template (the simulator's reason code in `message` is
  replaced);
* in default mode (`warn`) an ERROR on a declared requirement rejects, an ERROR on a derived requirement is only a
  warning with a label (AC-18), WARNING is a warning and UNVERIFIED is info; in `strict` every ERROR, WARNING and
  UNVERIFIED rejects (AC-19), except the "convert to schema" advisory, which stays info (AC-17);
* strict also demands schema-form output contracts (AC-17): under workflow strict the producer feeding a requirement
  gets an ERROR; a state-level strict override puts the ERROR on the consuming state, naming the producer;
* `require_input_contracts` turns every uncovered reachable node into a COVERAGE ERROR that rejects in both modes
  (AC-21); without it the nodes are only listed;
* nothing here raises: a record the policy cannot render degrades to a fixed message, a policy-wide failure gives one
  graph-level "could not analyse" record that blocks under workflow or state strict, tagged with its cause.

Messages come from fixed templates. Every name that reaches one (state ids, field names, parameter names, paths) is
quoted, capped in length and stripped of anything outside a safe set, because the string error format joins messages
into `<br>` markup. Binding values and example-form contract text are never read here, so they cannot be echoed.

Imports only leaf modules (`workflows.constants` and sibling contracts modules).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from codemie.workflows.constants import ContractValidationMode
from codemie.workflows.contracts.derive import (
    NOTE_DECLARED_CONTRACT_UNUSABLE,
    ORIGIN_CONDITION,
    ORIGIN_ITER_KEY,
    ORIGIN_SWITCH,
    REASON_ITER_KEY_NOT_ARRAY,
    REASON_LIST_OUTPUT,
    REASON_NO_USABLE_OUTPUT_CONTRACT,
    REASON_NOT_IN_OUTPUT_CONTRACT,
)
from codemie.workflows.contracts.effects import (
    NOTE_DECLARED_FIELD_NOT_PRODUCED,
    NOTE_OUTPUT_CONTRACT_IGNORED,
    NOTE_TYPE_CONFLICT,
)
from codemie.workflows.contracts.input_contract import _safe_name
from codemie.workflows.contracts.models import (
    ContractAnalysisStatus,
    ContractLinkReport,
    ContractLocation,
    ContractRecord,
    Outcome,
    Provenance,
    RecordKind,
    ReportedAs,
    StrictSource,
)
from codemie.workflows.contracts.reader import ContractForm
from codemie.workflows.contracts.settings import ContractSettings, read_mode
from codemie.workflows.contracts.simulate_model import (
    REASON_DERIVATION_FAILED,
    REASON_DUPLICATE_STATE_ID,
    REASON_EFFECT_FAILED,
    REASON_ITERATION_ITEM_MAY_SUPPLY,
    REASON_NOT_GUARANTEED,
    REASON_PRODUCER_CONVERT_TO_SCHEMA,
    REASON_STATE_NOT_ANALYSABLE,
    REASON_TRANSITION_FAILED,
    REASON_TRANSITION_TARGET_MISSING,
    REASON_WORK_CAP_REACHED,
    OUTPUT_SCHEMA_PATH,
    Finding,
    SimulationResult,
    StateAnalysis,
)

logger = logging.getLogger(__name__)

# Cause tags of the graph-level "could not analyse" records; they are also the reason codes of their templates.
CAUSE_SIMULATION_FAILED = "simulation_failed"
CAUSE_POLICY_FAILED = "policy_failed"
CAUSE_SETTINGS_FAILED = "settings_failed"
CAUSE_WORK_CAP_REACHED = REASON_WORK_CAP_REACHED
# A record whose strict contract-form check raised: the consumer state is reported UNVERIFIED and still blocks.
REASON_FORM_CHECK_FAILED = "form_check_failed"

# Reason codes the policy itself creates.
REASON_STRICT_PRODUCER_NOT_SCHEMA_FORM = "strict_producer_not_schema_form"
REASON_STRICT_CONSUMER_PRODUCER_NOT_SCHEMA_FORM = "strict_consumer_producer_not_schema_form"
REASON_COVERAGE_UNCOVERED = "coverage_uncovered"

# Labels of an ERROR on a derived requirement in default mode (AC-18). "renders empty" is set by the simulator.
LABEL_CERTAIN_FAILURE = "certain runtime failure"
LABEL_NOT_GUARANTEED = "not guaranteed by the contracts"

MAX_PATH_LENGTH = 200
MAX_MESSAGE_LENGTH = 500
COVERAGE_PATH = "input_schema"

_UNSAFE_PATH_CHARS = re.compile(r"[^A-Za-z0-9_.\-\[\]]")
# C0/C1 controls, DEL and the Unicode line/paragraph separators
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f-\x9f\u2028\u2029]")

# Reasons of an ERROR that is "not guaranteed by the contracts" rather than a certain runtime failure (AC-18).
_NOT_GUARANTEED_REASONS = frozenset({REASON_NOT_GUARANTEED, REASON_NOT_IN_OUTPUT_CONTRACT})
# Kinds whose ERROR rejects in default mode (AC-18, AC-21); a derived or convergence ERROR only warns.
_REJECTING_KINDS = frozenset({RecordKind.DECLARED, RecordKind.COVERAGE, RecordKind.CONTRACT_FORM})
# A per-state analysis failure means the state's coverage is unknown, so it must not also become a coverage ERROR.
_STATE_FAILURE_REASONS = frozenset({REASON_DERIVATION_FAILED, REASON_EFFECT_FAILED, REASON_TRANSITION_FAILED})
# Origins that check a node's own output, not what it reads from upstream producers.
_OWN_OUTPUT_ORIGINS = frozenset({ORIGIN_CONDITION, ORIGIN_SWITCH, ORIGIN_ITER_KEY})
_UPSTREAM_KINDS = frozenset({RecordKind.DECLARED, RecordKind.DERIVED})
# Forms of a declared output contract that are not schema form (AC-17); `none` declares nothing.
_DECLARED_NON_SCHEMA_FORMS = frozenset({ContractForm.EXAMPLE, ContractForm.LIST, ContractForm.INVALID})

MESSAGE_TEMPLATES: dict[str, str] = {
    "ok": "Requirement '{name}' of state '{state}' is satisfied.{producers_clause}",
    REASON_NOT_GUARANTEED: (
        "'{name}' needed by state '{state}' is not guaranteed by the contracts: no source on the path produces it."
        "{sources_clause}"
    ),
    "removed": "'{name}' needed by state '{state}' is removed before it runs.{removal_clause}",
    REASON_ITERATION_ITEM_MAY_SUPPLY: (
        "'{name}' needed by state '{state}' is excluded from the iteration context, but the iteration item may supply "
        "it, so it cannot be verified.{removal_clause}{sources_clause}"
    ),
    "write_suppressed": "'{name}' needed by state '{state}' is never written to the context.{write_clause}",
    "possibly_removed": "'{name}' needed by state '{state}' may be removed on the way to it.{removal_clause}",
    "not_on_every_alternative": (
        "'{name}' needed by state '{state}' is not produced on every alternative path.{lacking_clause}"
    ),
    "possibly_present": "'{name}' needed by state '{state}' may be absent when it runs.{producers_clause}",
    "produced_only_in_cycle": (
        "'{name}' needed by state '{state}' is produced only inside the cycle that contains it, so it cannot be "
        "verified.{producers_clause}"
    ),
    "unknown_source_on_path": (
        "'{name}' needed by state '{state}' cannot be verified: a source on the path has no output contract."
        "{sources_clause}"
    ),
    "type_mismatch": (
        "'{name}' needed by state '{state}' as {types} has a different type at its source.{producers_clause}"
    ),
    "type_unverified": (
        "The type of '{name}' needed by state '{state}' cannot be verified.{producers_clause}{overwritten_clause}"
    ),
    "optional_source": "'{name}' needed by state '{state}' is optional or nullable at its source.{producers_clause}",
    "context_value_wins": (
        "Parameter '{name}' is bound in state '{state}', but the shared context also holds a field with that name, "
        "and the context value wins at runtime."
    ),
    "tool_input_key_namespace": (
        "Parameter '{name}' of state '{state}' is looked up inside the tool input key, whose contents cannot be "
        "verified."
    ),
    "tool_schema_unknown": (
        "The parameter schema of the tool used by state '{state}' is unknown, so its bindings cannot be verified."
    ),
    "lookup_budget_exhausted": (
        "The tool schema lookup limit was reached before state '{state}', so its bindings cannot be verified."
    ),
    "mandatory_parameter_not_bound": "Mandatory tool parameter '{name}' is not bound in state '{state}'.",
    "multiple_parallel_writers": (
        "'{name}' is written by several parallel branches before state '{state}', so its final value depends on the "
        "order.{producers_clause}"
    ),
    "removal_races_write": (
        "'{name}' is removed in one parallel branch and written in another before state '{state}', so the outcome "
        "depends on the order.{producers_clause}"
    ),
    "producer_unknown_source": (
        "The output contract of state '{state}' names no fields, so later states cannot rely on it."
    ),
    REASON_PRODUCER_CONVERT_TO_SCHEMA: (
        "The output contract of state '{state}' is an example or has only recovered keys. Convert it to a JSON schema "
        "so that types can be verified."
    ),
    REASON_WORK_CAP_REACHED: "'{name}' of state '{state}' was not verified: the analysis stopped at its work limit.",
    REASON_DERIVATION_FAILED: "The requirements of state '{state}' could not be analysed.",
    REASON_EFFECT_FAILED: "The output of state '{state}' could not be analysed.",
    REASON_TRANSITION_FAILED: "The transitions of state '{state}' could not be analysed.",
    REASON_FORM_CHECK_FAILED: "The output contract form behind the inputs of state '{state}' could not be checked.",
    REASON_STATE_NOT_ANALYSABLE: "A state entry is not a workflow state and was not analysed.",
    REASON_DUPLICATE_STATE_ID: "State id '{state}' is declared more than once; only the first one is analysed.",
    REASON_TRANSITION_TARGET_MISSING: (
        "The transition at '{path}' of state '{state}' points to a state that does not exist."
    ),
    REASON_NO_USABLE_OUTPUT_CONTRACT: (
        "State '{state}' has no usable output contract, so '{name}' in its transition cannot be verified."
    ),
    REASON_NOT_IN_OUTPUT_CONTRACT: "'{name}' used in the transition of state '{state}' is not in its output contract.",
    REASON_LIST_OUTPUT: (
        "The output contract of state '{state}' is a list without field names, so '{name}' in its transition cannot "
        "be verified."
    ),
    REASON_ITER_KEY_NOT_ARRAY: (
        "The iteration key '{name}' of state '{state}' is declared as a type other than array in its output contract."
    ),
    NOTE_TYPE_CONFLICT: (
        "The output contract of state '{state}' disagrees with its own mappings on the type of '{name}'."
    ),
    NOTE_DECLARED_FIELD_NOT_PRODUCED: (
        "The output contract of state '{state}' declares '{name}', which its mappings do not always produce."
    ),
    NOTE_OUTPUT_CONTRACT_IGNORED: "The output contract of state '{state}' is ignored by this kind of node.",
    NOTE_DECLARED_CONTRACT_UNUSABLE: "The input contract of state '{state}' is unusable.",
    REASON_STRICT_PRODUCER_NOT_SCHEMA_FORM: (
        "State '{state}' feeds other states, but its output contract is not a JSON schema, which strict save "
        "validation requires."
    ),
    REASON_STRICT_CONSUMER_PRODUCER_NOT_SCHEMA_FORM: (
        "State '{state}' reads from state '{producer}', whose output contract is not a JSON schema, which strict "
        "save validation requires."
    ),
    REASON_COVERAGE_UNCOVERED: (
        "State '{state}' declares no input contract and none can be derived, and require_input_contracts is on."
    ),
    CAUSE_SIMULATION_FAILED: "The contract analysis failed and the workflow was not analysed.",
    CAUSE_POLICY_FAILED: "The contract analysis results could not be evaluated.",
    CAUSE_SETTINGS_FAILED: "The contract settings of the workflow could not be read and the workflow was not analysed.",
}

# A graph-level record has no state, so a few reasons read differently without one.
GRAPH_MESSAGE_TEMPLATES: dict[str, str] = {
    REASON_WORK_CAP_REACHED: "The analysis stopped at its work limit; the remaining requirements are not verified.",
}

_GENERIC_TEMPLATE = "Contract finding at '{path}' of state '{state}'."
_FALLBACK_MESSAGE = "This result could not be described."


# --- names ---------------------------------------------------------------------------------------


def safe_name(value: object) -> str:
    """A state id, field name or parameter name made safe for messages: conservative characters, bounded length."""
    return _safe_name(value)


def safe_path(value: object) -> str:
    """A YAML path made safe for messages: like `safe_name`, but keeps `[` and `]` and allows a longer path."""
    return _UNSAFE_PATH_CHARS.sub("_", str(value))[:MAX_PATH_LENGTH]


def raw_name(value: object) -> str:
    """A path or field name for a JSON field: as the client wrote it, minus control characters, length-capped.

    Unlike `safe_name` this keeps spaces and non-ASCII letters, so two different names stay different (they seed the
    issue id) and `config_path` points at the key that exists. Messages keep using the sanitised forms.
    """
    return _CONTROL_CHARS.sub("", str(value))[:MAX_PATH_LENGTH]


def _quote(names: Iterable[object]) -> str:
    return ", ".join(f"'{safe_name(name)}'" for name in names)


# --- modes ---------------------------------------------------------------------------------------


def resolve_state_modes(states: Iterable[Any], settings: ContractSettings) -> dict[str, ContractValidationMode]:
    """The effective mode of every state: its own `save_validation` when recognised, else the workflow mode (AC-20).

    Only members with a non-empty string `id` are listed (a duplicate id keeps the first); a `save_validation` value
    that is not `strict` or `warn` is the schema stage's to reject and inherits here.
    """
    modes: dict[str, ContractValidationMode] = {}
    for state in states:
        try:
            state_id = getattr(state, "id", None)
            if not isinstance(state_id, str) or not state_id or state_id in modes:
                continue
            modes[state_id] = read_mode(getattr(state, "save_validation", None), settings.mode)
        except Exception:
            logger.warning("Contract linker could not read the mode of a state", exc_info=True)
    return modes


# --- public entries ------------------------------------------------------------------------------


def build_report(result: SimulationResult, *, states: Iterable[Any], settings: ContractSettings) -> ContractLinkReport:
    """The finished report for one simulation: messages, `reported_as`, coverage records and coverage data.

    Never raises: a failure of the policy as a whole gives `failure_report(settings, CAUSE_POLICY_FAILED)`.
    """
    try:
        return _build_report(result, states, settings)
    except Exception:
        logger.warning("Contract linker policy failed", exc_info=True)
        return failure_report(settings, CAUSE_POLICY_FAILED, states=states)


def failure_report(settings: ContractSettings, cause: str, *, states: Iterable[Any] = ()) -> ContractLinkReport:
    """A report holding one graph-level "could not analyse" record, for a failure of the whole analysis.

    `cause` is the tag (`CAUSE_SIMULATION_FAILED` for an exception out of the simulator) and also the label of the
    record. Strict on the workflow or on any state blocks it (AC-19); otherwise it is info. `analysed` stays False.
    """
    modes = _safe_modes(states, settings)
    strict = ContractValidationMode.STRICT
    rejects = settings.mode is strict or any(mode is strict for mode in modes.values())
    # An unreadable settings block is a fail-closed assumption of strict, not something the author set.
    strict_source = _strict_level(settings) if rejects and cause != CAUSE_SETTINGS_FAILED else None
    record = ContractRecord(
        kind=RecordKind.ANALYSIS_FAILURE,
        outcome=Outcome.UNVERIFIED,
        consumer_state_id="",
        path="",
        location=ContractLocation.workflow(),
        message=MESSAGE_TEMPLATES.get(cause, _FALLBACK_MESSAGE).format_map(_Blank()),
        label=cause,
        reported_as=ReportedAs.BLOCKS if rejects else ReportedAs.INFO,
        strict_source=strict_source,
    )
    return ContractLinkReport(status=ContractAnalysisStatus.FAILED, records=(record,), effective_modes=modes)


def render_message(finding: Finding) -> str:
    """The fixed-template message of one finding. Raises only if the finding's own structure is unreadable."""
    record = finding.record
    reason = finding.reason
    if record.consumer_state_id == "" and reason in GRAPH_MESSAGE_TEMPLATES:
        template = GRAPH_MESSAGE_TEMPLATES[reason]
    else:
        template = MESSAGE_TEMPLATES.get(reason, _GENERIC_TEMPLATE)
    return template.format_map(_fragments(finding))[:MAX_MESSAGE_LENGTH]


# --- report assembly -----------------------------------------------------------------------------


def _build_report(result: SimulationResult, states: Iterable[Any], settings: ContractSettings) -> ContractLinkReport:
    modes = resolve_state_modes(states, settings)
    records = []
    for finding in result.findings:
        mode = _mode_of(finding.record, modes, settings)
        records.append(_finish(finding, mode, strict_source=_strict_source(finding, mode, settings)))
    records.extend(
        _finish(finding, ContractValidationMode.STRICT, force=ReportedAs.BLOCKS, strict_source=_strict_level(settings))
        for finding in _strict_contract_form_findings(result, modes, settings)
    )
    if settings.require_input_contracts:
        records.extend(
            _finish(finding, modes.get(finding.record.consumer_state_id, settings.mode), force=ReportedAs.BLOCKS)
            for finding in _coverage_findings(result)
        )
    return ContractLinkReport(
        status=ContractAnalysisStatus.ANALYSED,
        records=tuple(records),
        cap_reached=result.cap_reached,
        validated_count=result.validated_count,
        reachable_total=result.reachable_total,
        uncovered_state_ids=tuple(result.uncovered_state_ids),
        unreachable_state_ids=tuple(result.unreachable_state_ids),
        effective_modes=modes,
    )


def _safe_modes(states: Iterable[Any], settings: ContractSettings) -> dict[str, ContractValidationMode]:
    try:
        return resolve_state_modes(states, settings)
    except Exception:
        logger.warning("Contract linker could not resolve state modes", exc_info=True)
        return {}


def _mode_of(
    record: ContractRecord, modes: Mapping[str, ContractValidationMode], settings: ContractSettings
) -> ContractValidationMode:
    return modes.get(record.consumer_state_id, settings.mode)


def _strict_level(settings: ContractSettings) -> StrictSource:
    """`workflow` when the workflow-level mode is strict, else `state`: a state's own override made it strict."""
    return StrictSource.WORKFLOW if settings.mode is ContractValidationMode.STRICT else StrictSource.STATE


def _strict_source(finding: Finding, mode: ContractValidationMode, settings: ContractSettings) -> StrictSource | None:
    """Set only when the record blocks in strict and would not block in warn; None for every other record."""
    strict, warn = ContractValidationMode.STRICT, ContractValidationMode.WARN
    if mode is not strict or _reported_as(finding, warn) is ReportedAs.BLOCKS:
        return None
    return _strict_level(settings) if _reported_as(finding, strict) is ReportedAs.BLOCKS else None


def _finish(
    finding: Finding,
    mode: ContractValidationMode,
    *,
    force: ReportedAs | None = None,
    strict_source: StrictSource | None = None,
) -> ContractRecord:
    """The finished copy of one finding's record; a record that cannot be rendered degrades to a fixed message.

    `strict_source` is decided by the caller, like `reported_as`, and survives a record that cannot be rendered.
    """
    record = finding.record
    # Decided before anything is rendered: a message that cannot be built must never change whether a record rejects.
    reported_as = force or _reported_as(finding, mode)
    # The JSON fields keep the raw names (see `raw_name`); only the message text uses the sanitised ones
    field = None if record.field is None else raw_name(record.field)
    try:
        return record.model_copy(
            update={
                "message": render_message(finding),
                "reported_as": reported_as,
                "label": _label(finding),
                "strict_source": strict_source,
                "path": raw_name(record.path),
                "field": field,
                "provenance": _clean_provenance(record.provenance),
            }
        )
    except Exception:
        logger.warning(
            "Contract linker could not render a record of state %s", safe_name(record.consumer_state_id), exc_info=True
        )
        return ContractRecord(
            kind=record.kind,
            outcome=record.outcome,
            consumer_state_id=record.consumer_state_id,
            path=raw_name(record.path),
            location=record.location,
            field=field,
            message=_FALLBACK_MESSAGE,
            reported_as=reported_as,
            strict_source=strict_source,
        )


def _reported_as(finding: Finding, mode: ContractValidationMode) -> ReportedAs:
    """AC-18 (default mode) and AC-19 (strict) for one record."""
    record = finding.record
    if record.outcome is Outcome.OK or finding.reason == REASON_PRODUCER_CONVERT_TO_SCHEMA:
        return ReportedAs.INFO
    if mode is ContractValidationMode.STRICT:
        return ReportedAs.BLOCKS
    if record.outcome is Outcome.ERROR:
        return ReportedAs.BLOCKS if record.kind in _REJECTING_KINDS else ReportedAs.WARNING
    if record.outcome is Outcome.WARNING:
        return ReportedAs.WARNING
    return ReportedAs.INFO


def _label(finding: Finding) -> str | None:
    record = finding.record
    if record.label:
        return record.label
    if record.kind is RecordKind.ANALYSIS_FAILURE and record.consumer_state_id == "":
        return finding.reason
    if record.outcome is Outcome.ERROR and record.kind is RecordKind.DERIVED:
        return LABEL_NOT_GUARANTEED if finding.reason in _NOT_GUARANTEED_REASONS else LABEL_CERTAIN_FAILURE
    return None


def _clean_provenance(provenance: Provenance) -> Provenance:
    return Provenance(
        producers=tuple(safe_name(name) for name in provenance.producers),
        sources_checked=tuple(safe_name(name) for name in provenance.sources_checked),
        overwritten_by=None if provenance.overwritten_by is None else safe_name(provenance.overwritten_by),
        removed_by=None if provenance.removed_by is None else safe_name(provenance.removed_by),
        mechanism=None if provenance.mechanism is None else safe_name(provenance.mechanism),
    )


# --- strict contract form (AC-17) and coverage (AC-21) -------------------------------------------


def _strict_contract_form_findings(
    result: SimulationResult, modes: Mapping[str, ContractValidationMode], settings: ContractSettings
) -> list[Finding]:
    """AC-17: strict requires schema-form output contracts.

    Under workflow strict every reachable producer that declares a non-schema output contract (example, list or
    invalid; a node that declares none is not flagged) gets an ERROR on itself, once, if its own mode is strict,
    whether or not anything consumes it. Under a state-level strict override of a consumer, in a workflow that is
    not strict, the ERROR goes on the consuming state and names the producer; there a producer is a state named in
    the provenance of a declared or derived requirement record, since only that link ties it to the consumer. The
    entry seeding, iteration items and nodes that write no context are never producers in this sense.
    """
    analyses = {item.state_id: item for item in result.states}
    seen: set[tuple[str, ...]] = set()
    found: list[Finding] = []
    _add_unseen(found, seen, _declared_contract_candidates(result, modes, settings))
    for finding in result.findings:
        try:
            candidates = _strict_candidates(finding, analyses, modes, settings)
        except Exception:
            logger.warning("Contract linker could not check the contract form for a record", exc_info=True)
            # Fail closed where strict applies: a bug here must not turn a strict rejection into a pass.
            candidates = _form_check_failure_candidates(finding.record.consumer_state_id, modes, settings)
        _add_unseen(found, seen, candidates)
    return found


def _add_unseen(
    found: list[Finding], seen: set[tuple[str, ...]], candidates: Iterable[tuple[tuple[str, ...], Finding]]
) -> None:
    for key, finding in candidates:
        if key not in seen:
            seen.add(key)
            found.append(finding)


def _form_check_failure_candidates(
    consumer: str, modes: Mapping[str, ContractValidationMode], settings: ContractSettings
) -> list[tuple[tuple[str, ...], Finding]]:
    strict = ContractValidationMode.STRICT
    if settings.mode is strict or modes.get(consumer) is strict:
        return [(("check_failed", consumer), _form_check_failure(consumer))]
    return []


def _declared_contract_candidates(
    result: SimulationResult, modes: Mapping[str, ContractValidationMode], settings: ContractSettings
) -> list[tuple[tuple[str, ...], Finding]]:
    strict = ContractValidationMode.STRICT
    if settings.mode is not strict:
        return []
    return [
        (
            ("producer", item.state_id),
            _form_finding(
                item.state_id,
                item.state_id,
                OUTPUT_SCHEMA_PATH,
                REASON_STRICT_PRODUCER_NOT_SCHEMA_FORM,
                location=ContractLocation.state(item.state_id, OUTPUT_SCHEMA_PATH),
            ),
        )
        for item in result.states
        if item.reachable
        and item.writes_context
        and not item.schema_form
        and item.contract_form in _DECLARED_NON_SCHEMA_FORMS
        and modes.get(item.state_id, settings.mode) is strict
    ]


def _form_check_failure(owner: str) -> Finding:
    record = ContractRecord(
        kind=RecordKind.ANALYSIS_FAILURE,
        outcome=Outcome.UNVERIFIED,
        consumer_state_id=owner,
        path="",
        location=ContractLocation.state(owner),
        message=REASON_FORM_CHECK_FAILED,
        label=REASON_FORM_CHECK_FAILED,
    )
    return Finding(record=record, reason=REASON_FORM_CHECK_FAILED)


def _form_finding(  # noqa: PLR0913 - keyword-only builder/internal helper; a parameter object would add indirection without clarity
    owner: str, producer: str, path: str, reason: str, field: str | None = None, *, location: ContractLocation | None
) -> Finding:
    form_record = ContractRecord(
        kind=RecordKind.CONTRACT_FORM,
        outcome=Outcome.ERROR,
        consumer_state_id=owner,
        path=path,
        location=location,
        field=field,
        message=reason,
        provenance=Provenance(producers=(producer,)),
    )
    return Finding(record=form_record, reason=reason)


def _strict_candidates(
    finding: Finding,
    analyses: Mapping[str, StateAnalysis],
    modes: Mapping[str, ContractValidationMode],
    settings: ContractSettings,
) -> list[tuple[tuple[str, ...], Finding]]:
    strict = ContractValidationMode.STRICT
    record, requirement = finding.record, finding.requirement
    if requirement is None or record.kind not in _UPSTREAM_KINDS or requirement.origin in _OWN_OUTPUT_ORIGINS:
        return []
    consumer = record.consumer_state_id
    candidates: list[tuple[tuple[str, ...], Finding]] = []
    for producer in record.provenance.producers:
        analysis = analyses.get(producer)
        if analysis is None or analysis.schema_form or not analysis.writes_context:
            continue
        if settings.mode is strict:
            if modes.get(producer, settings.mode) is not strict:
                continue
            key, owner = ("producer", producer), producer
            reason, path, field = REASON_STRICT_PRODUCER_NOT_SCHEMA_FORM, OUTPUT_SCHEMA_PATH, None
            location = ContractLocation.state(producer, OUTPUT_SCHEMA_PATH)
        elif modes.get(consumer, settings.mode) is strict:
            key, owner = ("consumer", consumer, producer), consumer
            reason, path, field = REASON_STRICT_CONSUMER_PRODUCER_NOT_SCHEMA_FORM, record.path, requirement.name
            location = record.location
        else:
            continue
        candidates.append((key, _form_finding(owner, producer, path, reason, field, location=location)))
    return candidates


def _coverage_findings(result: SimulationResult) -> list[Finding]:
    """AC-21: one COVERAGE ERROR per uncovered reachable node, except a node whose analysis failed."""
    failed = {
        item.record.consumer_state_id
        for item in result.findings
        if item.record.kind is RecordKind.ANALYSIS_FAILURE and item.reason in _STATE_FAILURE_REASONS
    }
    return [
        Finding(
            record=ContractRecord(
                kind=RecordKind.COVERAGE,
                outcome=Outcome.ERROR,
                consumer_state_id=state_id,
                path=COVERAGE_PATH,
                location=ContractLocation.state(state_id, COVERAGE_PATH),
                message=REASON_COVERAGE_UNCOVERED,
            ),
            reason=REASON_COVERAGE_UNCOVERED,
        )
        for state_id in result.uncovered_state_ids
        if state_id not in failed
    ]


# --- message fragments ---------------------------------------------------------------------------


class _Blank(dict):
    """`format_map` source for templates with no placeholders that still goes through the same call."""

    def __missing__(self, key: str) -> str:
        return ""


def _fragments(finding: Finding) -> dict[str, str]:
    record = finding.record
    provenance = record.provenance
    requirement = finding.requirement
    name = requirement.name if requirement is not None else _last_segment(record.path)
    types = requirement.types if requirement is not None and requirement.types else ()
    producers = provenance.producers
    return {
        "state": safe_name(record.consumer_state_id),
        "name": safe_name(name),
        "path": safe_path(record.path),
        "types": ", ".join(safe_name(item) for item in types) or "its declared type",
        "producer": safe_name(producers[0]) if producers else "",
        "producers_clause": f" Produced by {_quote(producers)}." if producers else "",
        "sources_clause": f" Sources checked: {_quote(provenance.sources_checked)}."
        if provenance.sources_checked
        else "",
        "lacking_clause": f" Missing on: {_quote(provenance.sources_checked)}." if provenance.sources_checked else "",
        "removal_clause": _node_clause("Removed by", "through", provenance),
        "write_clause": _node_clause("Not written by", "because of", provenance),
        "overwritten_clause": (
            f" It may have been overwritten by '{safe_name(provenance.overwritten_by)}'."
            if provenance.overwritten_by
            else ""
        ),
    }


def _node_clause(prefix: str, link: str, provenance: Provenance) -> str:
    if not provenance.removed_by and not provenance.mechanism:
        return ""
    node = f" {prefix} '{safe_name(provenance.removed_by)}'" if provenance.removed_by else f" {prefix} an earlier node"
    how = f" {link} {safe_name(provenance.mechanism)}" if provenance.mechanism else ""
    return f"{node}{how}."


def _last_segment(path: str) -> str:
    return path.rsplit(".", 1)[-1] if path else ""


__all__: Sequence[str] = (
    "CAUSE_POLICY_FAILED",
    "CAUSE_SETTINGS_FAILED",
    "CAUSE_SIMULATION_FAILED",
    "CAUSE_WORK_CAP_REACHED",
    "GRAPH_MESSAGE_TEMPLATES",
    "LABEL_CERTAIN_FAILURE",
    "LABEL_NOT_GUARANTEED",
    "MESSAGE_TEMPLATES",
    "build_report",
    "failure_report",
    "render_message",
    "resolve_state_modes",
    "safe_name",
    "safe_path",
)
