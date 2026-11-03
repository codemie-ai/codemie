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

"""The two coverage floors, the single result shape, and the fixed order they are applied in.

This module exists so that no consumer chooses its own evaluation order and no builder
invents a representation for "there is no figure".

**`F` first, then `E`.** `F` — the coverage floor — runs over the whole requested range and
decides whether a figure *exists*. `E` — the explicit-identity floor — is then applied to the
portion `F` admitted, and only *labels* it. `E` is never consulted about the sub-`F` portion,
because labelling nothing is not a weaker claim than `unavailable`; it is a different and false
one.

The wrong order is the plausible one, so here it is worked. With ``F = 2026-08-01`` and
``E = 2026-08-15``, a request for 15 Jul - 31 Aug returns **one** result: the August figure,
labelled partial in the project dimension because it reaches below `E`, **and** flagged
unavailable for 15-31 Jul because it reaches below `F`. Not unavailable for the whole range,
and not a partial-labelled figure quietly covering July. A unit that evaluates `E` first
returns the August subtotal with no sub-`F` flag at all - a truncated total presented as whole.

**`E >= F` is deliberately not asserted.** A retention republication raises `F` on purpose and
can raise it past `E`; the consequence is benign - every admitted row then carries explicit
identity and `E` labels nothing - and the evaluation order already produces the right answer.
An implementation that asserted the ordering on every read would fire on a legitimate
recovery path.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from enum import Enum
from types import MappingProxyType
from typing import Mapping

DEFAULT_EMISSION_LAG = timedelta(hours=24)
DEFAULT_MAX_ELASTICSEARCH_IDS = 50_000


class SpendDimension(str, Enum):
    """A dimension a figure can be incomplete in.

    `PROJECT` is scoped by the published floor `E`, so its portion is a time interval.
    `IDENTITY_BRIDGE` is the one dimension whose portion is not computable at all. The rest
    are scattered rows inside a fully covered period that no interval can express.
    """

    PROJECT = "project"
    IDENTITY_BRIDGE = "identity_bridge"
    WORKFLOW = "workflow"
    ASSISTANT = "assistant"
    CONVERSATION = "conversation"
    EXECUTION = "execution"
    DATASOURCE = "datasource"

    #: Grouped money per model. **Backed by a column the published schema already carries and
    #: already names as this**: `model_group`'s own contract comment reads "the CodeMie-facing alias,
    #: and the grouping dimension. Not the same as `model`, which is fully qualified - grouping by
    #: the wrong one splits or merges models unintentionally." So this is not a sixth dimension
    #: proposal against AD-29's closed list; it is the dimension the schema was given.
    MODEL = "model"

    #: The rows no traffic class claims - the residue of a class split. A portion, never a floor:
    #: scattered rows inside a fully covered period, exactly as a workflow hole is.
    TRAFFIC_CLASS = "traffic_class"


@dataclass(frozen=True, slots=True)
class TimeRange:
    """A half-open UTC interval: ``start <= t < end``.

    Half-open at both ends is not a detail. A closed upper bound is exactly the ``BETWEEN``
    inclusivity that lands a boundary row in two adjacent period totals, and adjacent
    half-open periods tile exactly: no row counted twice, none lost.
    """

    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        for name, value in (("start", self.start), ("end", self.end)):
            if value.tzinfo is None or value.utcoffset() != timedelta(0):
                raise ValueError(f"TimeRange.{name} must be UTC-aware, got {value!r}")
        if self.start >= self.end:
            raise ValueError(f"TimeRange must be non-empty and forward: {self.start!r} .. {self.end!r}")

    def widened(self, by: timedelta) -> TimeRange:
        """Both ends pushed out by `by` - the Elasticsearch scope window.

        A metric document's timestamp is *emission* time and never assigns a row to a period;
        only `start_time` does. So the Elasticsearch window's only job is to be a **superset**
        of the ids the period needs, never equal to the report range.
        """
        return TimeRange(self.start - by, self.end + by)


@dataclass(frozen=True, slots=True)
class Portion:
    """The part of a figure a dimension does not name.

    `spend` is what a money-bearing surface renders, and `rows` is a diagnostic reported
    *beside* it, never instead of it: 92.4% of rows carry 1.33% of spend, so a row-based
    portion misstates a money figure by more than an order of magnitude.
    """

    spend: Decimal | None
    rows: int | None
    sub_range: TimeRange | None = None


def _freeze(entries: Mapping[SpendDimension, Portion] | None = None) -> Mapping[SpendDimension, Portion]:
    return MappingProxyType(dict(entries or {}))


@dataclass(frozen=True, slots=True)
class SpendResult:
    """The one shape every ranged read returns.

    A consumer that renders only `figure` has dropped an annotation rather than simplified a
    call. The two annotations are independent, may both be present, and neither is ever
    collapsed into the other or dropped because the other fired.
    """

    figure: Decimal | None
    unavailable: TimeRange | None = None
    partial: Mapping[SpendDimension, Portion] = field(default_factory=_freeze)

    def __post_init__(self) -> None:
        object.__setattr__(self, "partial", _freeze(self.partial))

        if self.figure is None:
            # `figure is None` is the whole of the no-figure representation, and it means
            # exactly one thing: no portion of the requested range was admitted. There is
            # nothing to label, and the flag must name the time it covers - a result carrying
            # no figure and no flag names no absence at all.
            if self.unavailable is None:
                raise ValueError("figure is None requires an unavailable range naming the whole request")
            if self.partial:
                raise ValueError(f"figure is None admits no portion, so it cannot be partial in {sorted(self.partial)}")

        for dimension, portion in self.partial.items():
            if portion.spend is None and portion.rows is None and dimension is not SpendDimension.IDENTITY_BRIDGE:
                # An unresolvable alias set means the rows cannot be *identified*, so there is
                # no row set to sum or count. Every other portion holds its rows and is
                # missing only a key, so a None there is a builder defect, not a state.
                raise ValueError(f"{dimension.value} portion must be computable; only identity_bridge may be unknown")
            if portion.sub_range is not None and dimension not in _FLOOR_SCOPED_DIMENSIONS:
                raise ValueError(f"{dimension.value} has no published floor, so its portion cannot be a time interval")

    @property
    def is_exact(self) -> bool:
        """A real figure, complete in every dimension it depends on."""
        return self.figure is not None and not self.partial and self.unavailable is None

    @classmethod
    def no_figure(cls, requested: TimeRange) -> SpendResult:
        """No portion admitted: the range lies wholly below `F`, or readiness is invalidated.

        This is the constructor that stops the fabricated zero, which is what an
        implementation produces by itself: ``[max(start, F), end)`` read literally over a
        wholly-sub-`F` range is an inverted interval, and summing an inverted interval
        returns 0. A measured zero and an absent figure are different facts and only one of
        them may be shown as ``$0.00``.
        """
        return cls(figure=None, unavailable=requested)


#: Only a *published* floor scopes a portion as a time interval. Manufacturing a floor for a
#: dimension that has none - most plausibly "rows below the rollout instant" - is the third
#: floor this design refuses, arriving through the back door.
_FLOOR_SCOPED_DIMENSIONS = frozenset({SpendDimension.PROJECT})


@dataclass(frozen=True, slots=True)
class LedgerCoverage:
    """The four boundary and bounding values, read **once** at the composition root.

    That single read is what makes activation atomic with respect to readers: a floor is a
    deployment-level value every consumer observes at the same instant, never one that two
    surfaces can see differently. None of the four is ever derived from a query - in
    particular `F` is never a live ``MIN(start_time)``, which is a minimum over a table still
    being filled and reports coverage the table does not have.
    """

    coverage_floor: datetime | None = None
    explicit_identity_floor: datetime | None = None
    emission_lag: timedelta = DEFAULT_EMISSION_LAG
    max_elasticsearch_ids: int = DEFAULT_MAX_ELASTICSEARCH_IDS
    ready: bool = False

    @property
    def activated(self) -> bool:
        """Is the ledger answering at all?

        False before a first activation and False while readiness stands invalidated under
        either continuing invariant - retention deleting at or above `F`, or per-request spend
        logging having stopped. In that state every read is `unavailable`, id-keyed reads
        included, and no read path re-derives this: it is a state a named unit holds.
        """
        return self.ready and self.coverage_floor is not None

    def admitted(self, requested: TimeRange) -> TimeRange | None:
        """The portion of `requested` a figure exists for, or None if that is nothing.

        The first of the two floors, applied over the whole requested range.
        """
        if not self.activated:
            return None
        floor = self.coverage_floor
        assert floor is not None  # implied by `activated`, restated for the type checker
        start = max(requested.start, floor)
        return TimeRange(start, requested.end) if start < requested.end else None

    def unavailable_flag(self, requested: TimeRange) -> TimeRange | None:
        """The absence `requested` carries, if any - the two causes and their two formulas.

        **Floor cause:** the range reaches below `F`, whether crossing it or lying wholly
        beneath, so the flag is ``[start, min(end, F))``.

        **Readiness cause:** an unready ledger admits nothing *anywhere*, so the flag is the
        **whole** requested range. Reaching for the floor formula here computes an *empty*
        interval whenever ``start >= F``, and a result carrying no figure would then carry a
        flag naming no time at all - the opposite of what the shape requires.
        """
        if not self.activated:
            return requested
        floor = self.coverage_floor
        assert floor is not None
        return TimeRange(requested.start, min(requested.end, floor)) if requested.start < floor else None

    def project_dimension_portion(self, admitted: TimeRange) -> TimeRange | None:
        """The sub-`E` interval of an already-admitted range, or None if `E` labels nothing.

        The second floor, applied to the portion `F` admitted and to nothing else. An unset
        `E` means every period lies below it, which is a defined state with a defined exit and
        a correct steady state for a deployment that never rolls explicit identity out.
        """
        explicit_floor = self.explicit_identity_floor
        if explicit_floor is None:
            return admitted
        return TimeRange(admitted.start, min(admitted.end, explicit_floor)) if admitted.start < explicit_floor else None
