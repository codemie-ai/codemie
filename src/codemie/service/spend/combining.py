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

"""How several answers about one period become one answer, in the one place both composers read.

This is a rule rather than a helper, and it lives here because **two** units now compose
`SpendResult`s along two different axes. `spend_service` composes across **ledgers** - the call-class
split, where in a partly-LiteLLM deployment a holder's chat and embedding work are recorded in
different places and somebody has to add them up. The time cutover composes across **time** - one
requested period served by the spend-logs ledger at and above the published coverage floor `F` and by
the legacy Elasticsearch engine below it, because production carries no ledger rows before the
rollout. The axes are unrelated; what travels along them is identical, because in both cases the
pieces are answers about *disjoint parts of one requested range*.

**Why one copy and not two, which is the whole reason this module exists.** A second copy does not
fail; it drifts. The rules here are each one line of code and each one wrong line away from a
plausible mistake - summing a missing figure as zero, dropping an unavailable flag because the other
contributor had a figure, adding two portions when one of them was never computable. A composer that
drifted on any of those would still return a number, and two surfaces would then disagree about one
holder's spend for one week: exactly the freedom the spend port was built to remove. Copying it to
serve the cutover would have re-opened that freedom in the same change that closed a different one.

**These are public names, deliberately.** Both composition paths - the port's own and the cutover -
call them, and a function shared across module boundaries is published rather than imported by a
leading-underscore name.
"""

from __future__ import annotations

from decimal import Decimal

from codemie.service.spend.coverage import Portion, SpendDimension, SpendResult, TimeRange

__all__ = ["combine_results", "widest_portion"]


def combine_results(results: list[SpendResult], requested: TimeRange) -> SpendResult:
    """Add several ledgers' answers into one, without losing what any of them said.

    Three things travel and none may be dropped:

    - **the figures**, summed over the ledgers that produced one. A ledger that produced none did not
      report a zero, so it contributes nothing to the sum rather than nothing to the total;
    - **the unavailable flag**, which survives if *any* ledger raised it. A figure missing one
      ledger's contribution is a partial total, and presenting it unflagged is the truncated total
      the whole design exists to prevent;
    - **the partial entries**, merged per dimension.

    When **no** ledger produced a figure the result carries none, and the flag then names the whole
    requested range - never a zero, which would claim nothing was spent.
    """
    figures = [result.figure for result in results if result.figure is not None]
    unavailable = next((result.unavailable for result in results if result.unavailable is not None), None)

    partial: dict[SpendDimension, Portion] = {}
    for result in results:
        for dimension, portion in result.partial.items():
            partial[dimension] = widest_portion(partial.get(dimension), portion)

    if not figures:
        # No portion admitted anywhere. The type forbids a partial label here, because there is no
        # figure for a label to qualify.
        return SpendResult.no_figure(unavailable or requested)

    return SpendResult(figure=sum(figures, Decimal("0")), unavailable=unavailable, partial=partial)


def widest_portion(existing: Portion | None, incoming: Portion) -> Portion:
    """Merge two portions naming the same dimension.

    An **unknown** amount wins over a known one: if either ledger could not compute its portion,
    the merged portion is not computable either, and rendering the half that *was* computable would
    understate a hole whose size nobody knows.
    """
    if existing is None:
        return incoming
    if existing.spend is None or incoming.spend is None:
        return Portion(spend=None, rows=None, sub_range=existing.sub_range or incoming.sub_range)
    return Portion(
        spend=existing.spend + incoming.spend,
        rows=(existing.rows or 0) + (incoming.rows or 0),
        sub_range=existing.sub_range or incoming.sub_range,
    )
