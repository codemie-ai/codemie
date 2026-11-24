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

"""Contract linker models (EPMCDME-16503): requirements, report records and the per-call `ContractLinkReport`.

`outcome` is the raw result of one check; `reported_as` is its class after the mode policy and stays `None` until
the policy has run. The two are kept apart on purpose: rejections are counted on `reported_as`, the corpus ceiling on
`outcome`. A finished `ContractLinkReport` holds only records the policy has classified. Imports only the leaf
constants module.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, model_validator

from codemie.workflows.constants import ContractValidationMode


class RecordKind(str, Enum):
    DECLARED = "declared"
    DERIVED = "derived"
    COVERAGE = "coverage"
    CONTRACT_FORM = "contract-form"
    CONVERGENCE = "convergence"
    ANALYSIS_FAILURE = "analysis-failure"


# The kind of a requirement is the kind of the record it becomes; only these two record kinds come from a requirement.
RequirementKind = Literal[RecordKind.DECLARED, RecordKind.DERIVED]


class Outcome(str, Enum):
    OK = "OK"
    ERROR = "ERROR"
    WARNING = "WARNING"
    UNVERIFIED = "UNVERIFIED"


class ReportedAs(str, Enum):
    BLOCKS = "blocks"
    WARNING = "warning"
    INFO = "info"


class LocationSection(str, Enum):
    STATES = "states"
    TOOLS = "tools"
    CUSTOM_NODES = "custom_nodes"
    WORKFLOW = "workflow"


class ContractLocation(BaseModel):
    """Internal source address, independent of diagnostic text and YAML line numbers.

    Segments are exact mapping keys or list indices. A numeric string is never an index.
    An empty segment tuple identifies the owning item, the fallback for an implicit field.
    The workflow form has no owner and can address a root field or the root itself.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    section: LocationSection
    owner_id: StrictStr | None = None
    segments: tuple[StrictStr | StrictInt, ...] = ()

    @model_validator(mode="after")
    def _validate_owner(self) -> Self:
        if self.section is LocationSection.WORKFLOW:
            if self.owner_id is not None:
                raise ValueError("a workflow location has no owner")
        elif not self.owner_id:
            raise ValueError("a section location needs an owner")
        return self

    @classmethod
    def state(cls, owner_id: str, *segments: str | int) -> Self:
        return cls(section=LocationSection.STATES, owner_id=owner_id, segments=segments)

    @classmethod
    def workflow(cls, *segments: str | int) -> Self:
        return cls(section=LocationSection.WORKFLOW, segments=segments)

    def child(self, *segments: str | int) -> Self:
        return type(self)(section=self.section, owner_id=self.owner_id, segments=(*self.segments, *segments))


class StrictSource(str, Enum):
    """Which level made a record block because of strict: the workflow-level mode, or a state's own override."""

    WORKFLOW = "workflow"
    STATE = "state"


class ContractRequirement(BaseModel):
    """One field a state needs from the shared context.

    `origin` is a fixed code naming where the need comes from (for example `input_schema`, `placeholder`,
    `extract`, `tool_binding`, `transition`); `path` is the location on the consumer. `types` is `None` when the
    consumer states no type.
    """

    model_config = ConfigDict(frozen=True)

    state_id: str
    path: str
    name: str
    kind: RequirementKind
    origin: str = ""
    types: tuple[str, ...] | None = None
    required: bool = True
    location: ContractLocation | None = None


class Provenance(BaseModel):
    """Who produced, overwrote or removed a field, and what was checked."""

    model_config = ConfigDict(frozen=True)

    producers: tuple[str, ...] = ()
    sources_checked: tuple[str, ...] = ()
    overwritten_by: str | None = None
    removed_by: str | None = None
    mechanism: str | None = None


class ContractRecord(BaseModel):
    """One requirement result or finding of the report."""

    kind: RecordKind
    outcome: Outcome
    reported_as: ReportedAs | None = None
    consumer_state_id: str
    path: str
    location: ContractLocation | None = None
    # The bare context-field or parameter name the record is about; None where no single field applies.
    field: str | None = None
    message: str = ""
    label: str | None = None
    # Set only when the record blocks because of strict (it would not block in warn); None otherwise.
    strict_source: StrictSource | None = None
    provenance: Provenance = Field(default_factory=Provenance)


class ContractAnalysisStatus(str, Enum):
    """What happened to the analysis behind a report, so "off" and "crashed" are never read as the same thing."""

    NOT_RUN = "not_run"  # the linker is off: nothing was analysed and nothing is reported
    FAILED = "failed"  # the analysis could not run or be evaluated: one graph-level record says so
    ANALYSED = "analysed"  # the simulation and the policy ran; `cap_reached` tells whether the work limit cut it short


class ContractLinkReport(BaseModel):
    """Result of one linker call: per-requirement records plus coverage data. Never shared between calls.

    Immutable: the policy builds the record list first and constructs the report from it. The rules the consumers
    rely on are enforced on construction, so a violation fails where the report is built (the policy turns that into
    a blocking failure report) and never reaches a consumer as a record that looks advisory:

    - every record is classified (`reported_as` is set), because a consumer treats anything that is not `blocks` as
      advisory;
    - a record that blocks is never an `OK` one;
    - `NOT_RUN` carries no records, and `FAILED` carries the graph-level failure record that says why.

    `effective_modes` is a plain dict and is not made read-only by `frozen`; nothing writes to it after construction.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: ContractAnalysisStatus = ContractAnalysisStatus.NOT_RUN
    records: tuple[ContractRecord, ...] = ()
    cap_reached: bool = False
    validated_count: int = 0
    reachable_total: int = 0
    uncovered_state_ids: tuple[str, ...] = ()
    unreachable_state_ids: tuple[str, ...] = ()
    effective_modes: dict[str, ContractValidationMode] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _enforce_rules(self) -> Self:
        for record in self.records:
            if record.reported_as is None:
                raise ValueError("a report record must be classified: reported_as is not set")
            if record.reported_as is ReportedAs.BLOCKS and record.outcome is Outcome.OK:
                raise ValueError("a record with outcome OK cannot block")
        if self.status is ContractAnalysisStatus.NOT_RUN and self.records:
            raise ValueError("a report that was not run carries no records")
        if self.status is ContractAnalysisStatus.FAILED and not any(
            record.kind is RecordKind.ANALYSIS_FAILURE and not record.consumer_state_id for record in self.records
        ):
            raise ValueError("a failed report carries the graph-level analysis failure record")
        return self

    def blocking_records(self) -> list[ContractRecord]:
        return [record for record in self.records if record.reported_as is ReportedAs.BLOCKS]

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")

    def to_json(self) -> str:
        return self.model_dump_json()
