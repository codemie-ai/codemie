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

"""Values every part of the availability simulator shares (EPMCDME-16503, parent §4.1, §4.2).

The reason codes a finding carries, the paths it is reported at, the three result shapes `simulate_workflow` returns
and the small helpers that read a state or a tool section defensively. It imports nothing from the other
`simulate_*` modules, so each of them can use it without a cycle.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any

from codemie.workflows.contracts.abstract_context import FieldStatus
from codemie.workflows.contracts.effects import NodeKind
from codemie.workflows.contracts.models import ContractRecord, ContractRequirement
from codemie.workflows.contracts.reader import ContractForm

# Provenance lists reach `<br>`-joined markup, so they are bounded here as well as capped per name by T7.
MAX_PROVENANCE_NAMES = 8

# Path prefix of a finding that is about a context field rather than a location in the YAML (convergence records).
CONTEXT_PATH_PREFIX = "context_store."
# Path of the finding that says a tool's parameter schema could not be obtained.
TOOL_ARGS_PATH = "tool_args"
# Path of a producer-side contract-form finding.
OUTPUT_SCHEMA_PATH = "output_schema"
# Source name of the iteration item: its keys cannot be known at save time (AC-9), so it is an unknown source for the
# first node of a branch and, through `unknown_contribution`, for the rest of that branch.
ITERATION_ITEM_SOURCE = "iteration_item"

REASON_OK = "ok"
REASON_NOT_GUARANTEED = "not_guaranteed"
REASON_REMOVED = "removed"
REASON_ITERATION_ITEM_MAY_SUPPLY = "iteration_item_may_supply"
REASON_WRITE_SUPPRESSED = "write_suppressed"
REASON_POSSIBLY_REMOVED = "possibly_removed"
REASON_NOT_ON_EVERY_ALTERNATIVE = "not_on_every_alternative"
REASON_POSSIBLY_PRESENT = "possibly_present"
REASON_PRODUCED_ONLY_IN_CYCLE = "produced_only_in_cycle"
REASON_UNKNOWN_SOURCE_ON_PATH = "unknown_source_on_path"
REASON_TYPE_MISMATCH = "type_mismatch"
REASON_TYPE_UNVERIFIED = "type_unverified"
REASON_OPTIONAL_SOURCE = "optional_source"
REASON_CONTEXT_VALUE_WINS = "context_value_wins"
REASON_TOOL_INPUT_KEY_NAMESPACE = "tool_input_key_namespace"
REASON_TOOL_SCHEMA_UNKNOWN = "tool_schema_unknown"
REASON_LOOKUP_BUDGET_EXHAUSTED = "lookup_budget_exhausted"
REASON_MANDATORY_PARAMETER_NOT_BOUND = "mandatory_parameter_not_bound"
REASON_MULTIPLE_PARALLEL_WRITERS = "multiple_parallel_writers"
REASON_REMOVAL_RACES_WRITE = "removal_races_write"
REASON_PRODUCER_UNKNOWN_SOURCE = "producer_unknown_source"
REASON_PRODUCER_CONVERT_TO_SCHEMA = "producer_convert_to_schema"
REASON_WORK_CAP_REACHED = "work_cap_reached"
REASON_DERIVATION_FAILED = "derivation_failed"
REASON_EFFECT_FAILED = "effect_failed"
REASON_TRANSITION_FAILED = "transition_failed"
REASON_STATE_NOT_ANALYSABLE = "state_not_analysable"
REASON_DUPLICATE_STATE_ID = "duplicate_state_id"
REASON_TRANSITION_TARGET_MISSING = "transition_target_missing"

PRESENT_STATUSES = frozenset({FieldStatus.AVAILABLE, FieldStatus.PARTIALLY_AVAILABLE, FieldStatus.POSSIBLY_PRESENT})


class EdgeKind(str, Enum):
    """How one transition reaches its target, which decides the merge rule at the target (AC-6, AC-9)."""

    SINGLE = "single"
    ALTERNATIVE = "alternative"
    PARALLEL = "parallel"
    ITERATION = "iteration"


# --- public result shapes ------------------------------------------------------------------------


@dataclass(frozen=True)
class Finding:
    """One record plus the structured cause T7 needs to class it and render its message.

    `record.message` holds `reason`, a fixed code from this module's `REASON_*` constants; `detail` is the
    classifier's own reason (for example `list_without_dict_items`) when there is one.
    """

    record: ContractRecord
    reason: str
    requirement: ContractRequirement | None = None
    detail: str = ""


@dataclass(frozen=True)
class StateAnalysis:
    """What the pass learnt about one state. `analysed` is False when derivation, the effect or the transition raised,
    or when the work cap stopped the pass before this state."""

    state_id: str
    reachable: bool = False
    analysed: bool = False
    covered: bool = False
    coverage_source: str = ""
    kind: NodeKind = NodeKind.UNKNOWN
    known_source: bool = False
    schema_form: bool = False
    contract_form: ContractForm = ContractForm.NONE
    writes_context: bool = True
    iteration_depth: int = 0


@dataclass(frozen=True)
class SimulationResult:
    """Result of one pass. `findings` carry raw outcomes only: `reported_as` stays `None` until the mode policy runs."""

    findings: tuple[Finding, ...] = ()
    states: tuple[StateAnalysis, ...] = ()
    reachable_state_ids: tuple[str, ...] = ()
    unreachable_state_ids: tuple[str, ...] = ()
    uncovered_state_ids: tuple[str, ...] = ()
    validated_count: int = 0
    cap_reached: bool = False
    operations: int = 0
    lookups: int = 0

    @property
    def records(self) -> tuple[ContractRecord, ...]:
        return tuple(item.record for item in self.findings)

    @property
    def reachable_total(self) -> int:
        return len(self.reachable_state_ids)


def text_of(value: object) -> str:
    return value if isinstance(value, str) and value else ""


def index_by_id(items: Sequence[Any] | None) -> dict[str, Any]:
    """Index a `custom_nodes` / `tools` section by `id`, as `find_custom_node_by_id` and `ToolNode` match them."""
    indexed: dict[str, Any] = {}
    for item in items or ():
        key = text_of(getattr(item, "id", None))
        if key and key not in indexed:
            indexed[key] = item
    return indexed


def join_names(*groups: Iterable[str]) -> tuple[str, ...]:
    joined: dict[str, None] = {}
    for group in groups:
        for name in group:
            if name:
                joined.setdefault(name)
            if len(joined) >= MAX_PROVENANCE_NAMES:
                return tuple(joined)
    return tuple(joined)
