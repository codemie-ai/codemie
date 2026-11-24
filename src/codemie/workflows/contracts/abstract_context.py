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

"""The abstract shared context and its starting point (EPMCDME-16503, parent §4.1).

`FieldState` and `AbstractContext` live here rather than in `models.py`, because `models.py` holds the report models the
API sibling serialises, while these are simulation values consumed by `transitions.apply_transition` and the simulator
only. Both are treated as immutable: every function returns a new context.
"""

from __future__ import annotations

import dataclasses
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any, cast

from codemie.workflows.constants import FILE_NAMES_CONTEXT_KEY, PROPAGATED_HEADERS_CONTEXT_KEY
from codemie.workflows.contracts.derive import PLATFORM_CONTEXT_KEYS
from codemie.workflows.contracts.reader import ContractField, read_input_contract_fields

logger = logging.getLogger(__name__)

# Producer name of the entry seeding (`workflow.py:1189-1210`).
INITIAL_INPUT_SOURCE = "initial_input"

TYPE_STRING = "string"
TYPE_ARRAY = "array"

# Types of the platform keys seeded at the entry; a key with no entry here is seeded with an unknown type.
_PLATFORM_TYPES = {FILE_NAMES_CONTEXT_KEY: (TYPE_ARRAY,), PROPAGATED_HEADERS_CONTEXT_KEY: ("object",)}


class FieldStatus(str, Enum):
    """Parent §4.1 field statuses. `apply_transition` produces AVAILABLE, REMOVED and POSSIBLY_PRESENT; the other two
    come from the simulator's branch merge."""

    AVAILABLE = "available"
    PARTIALLY_AVAILABLE = "partially_available"
    POSSIBLY_PRESENT = "possibly_present"
    POSSIBLY_REMOVED = "possibly_removed"
    REMOVED = "removed"


class Mechanism(str, Enum):
    """Why a field is gone, named on the finding (AC-4). The values are the configuration that caused it."""

    FULL_RESET = "clear_context_store"
    KEEP_CURRENT = "clear_context_store_keep_current"
    RESET_KEYS = "reset_keys_in_context_store"
    OUTPUT_WRITING_OFF = "store_in_context_off"
    ITERATION_FILTER = "include_in_iterator_context"
    NO_CONTEXT_WRITE = "node_writes_no_context"


@dataclass(frozen=True)
class FieldState:
    """The abstract state of one context field (parent §4.1): status, provenance, iteration scope and types.

    `types_certain` is `False` once an unknown source may have overwritten the value, which is what makes the type
    UNVERIFIED (AC-2); `types is None` already means the contract stated no type (AC-7).

    `hidden` is the main-context state of a field that the iteration filter of `hidden_by` kept out of the branch:
    the main context still holds it, so it replaces the branch's state once that iteration finishes. It sits on the
    filter's REMOVED state, and on a merge of branch paths where some still hide the field, whatever the branch
    status; any other change to the field inside the branch drops it, because that change reaches the main context.
    """

    status: FieldStatus = FieldStatus.AVAILABLE
    producers: tuple[str, ...] = ()
    overwritten_by: str | None = None
    removed_by: str | None = None
    mechanism: Mechanism | None = None
    scope: int = 0
    types: tuple[str, ...] | None = None
    types_certain: bool = False
    optional: bool = False
    hidden: FieldState | None = dataclasses.field(default=None, compare=False, repr=False)
    hidden_by: str | None = dataclasses.field(default=None, compare=False, repr=False)

    def unhidden(self) -> FieldState:
        """This state without the main-context state: the branch changed the field, so the change is what remains."""
        if self.hidden is None and self.hidden_by is None:
            return self
        return cast(FieldState, dataclasses.replace(self, hidden=None, hidden_by=None))


@dataclass(frozen=True)
class AbstractContext:
    """The abstract shared context at one point of the graph.

    `scope` is 0 at the top level and the iteration depth inside a branch. `unknown_contribution` marks that some
    source on the path is unknown, so a field this context does not list may still exist (AC-5); `unknown_sources`
    names those sources. Removed fields are kept with status REMOVED, because a finding has to name the node and the
    mechanism that removed them (AC-4). `suppressions` maps a field name to `(node, mechanism)` for a write that a node
    on the path declared but did not perform; it travels with the context, so a finding blames only an ancestor.
    """

    fields: Mapping[str, FieldState] = dataclasses.field(default_factory=dict)
    unknown_contribution: bool = False
    unknown_sources: tuple[str, ...] = ()
    scope: int = 0
    suppressions: Mapping[str, tuple[str, str]] = dataclasses.field(default_factory=dict)

    def get(self, name: str) -> FieldState | None:
        return self.fields.get(name)


@dataclass(frozen=True)
class SuppressedWrite:
    """A field the node's contract declares but the transition does not write, and why (AC-4)."""

    name: str
    mechanism: Mechanism


@dataclass(frozen=True)
class TransitionResult:
    """`context` flows along the ordinary edges; `iteration_context` is what an iteration branch sees, or `None`.

    `operations` is the number of abstract operations performed, for the simulator's work cap.
    """

    context: AbstractContext
    iteration_context: AbstractContext | None = None
    suppressed: tuple[SuppressedWrite, ...] = ()
    operations: int = 0


def initial_context(
    *, unknown_initial_input: bool = True, declared: Sequence[ContractField] | None = None
) -> AbstractContext:
    """The context at the entry (`workflow.py:1189-1210`): the platform keys are always optional known sources
    (AC-S4).

    `declared` is the field list of the entry node's usable `input_schema` (AC-2, AC-5). A declared contract is the
    workflow's initial input: each field is an `initial_input` source with its declared types (optional unless
    required), and the initial input is no longer an unknown contribution, so a missing field can be an ERROR (AC-3).
    With no declared contract (`None`) an undeclared initial input is an unknown contribution, so nothing is an ERROR
    because of it (AC-5), unless the caller states otherwise through `unknown_initial_input`.
    """
    fields = {
        name: FieldState(
            status=FieldStatus.AVAILABLE,
            producers=(INITIAL_INPUT_SOURCE,),
            types=_PLATFORM_TYPES.get(name),
            types_certain=_PLATFORM_TYPES.get(name) is not None,
            optional=True,
        )
        for name in sorted(PLATFORM_CONTEXT_KEYS)
    }
    if declared is not None:
        unknown_initial_input = False
        for entry in declared:
            fields[entry.name] = FieldState(
                status=FieldStatus.AVAILABLE,
                producers=(INITIAL_INPUT_SOURCE,),
                types=entry.types,
                types_certain=entry.types is not None,
                optional=entry.required is not True,
            )
    return AbstractContext(
        fields=fields,
        unknown_contribution=unknown_initial_input,
        unknown_sources=(INITIAL_INPUT_SOURCE,) if unknown_initial_input else (),
    )


def entry_input_fields(entry: Any) -> tuple[ContractField, ...] | None:
    """The fields of the entry state's usable declared `input_schema`, or None when there is none (AC-2, AC-5).

    An empty `properties` object is a usable contract with no fields: the workflow input is declared to carry nothing.
    A value the AC-16 check rejects or that is not schema-form declares nothing and leaves the input unknown.
    """
    try:
        text = getattr(entry, "input_schema", None)
        return None if text is None else read_input_contract_fields(text)
    except Exception:
        logger.warning("Contract linker could not read the entry input contract", exc_info=True)
        return None
