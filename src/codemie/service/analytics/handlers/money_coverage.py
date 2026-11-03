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

"""Whether an Elasticsearch money sum is whole, said in the vocabulary the money surfaces already use.

**A `sum` over `attributes.money_spent` is not a total.** The monitoring services omit that attribute
deliberately whenever a cost is unsettled - a streaming response carries no `x-litellm-response-cost`
header, so there is no cost to read and the document is emitted with its final token counts and no
money at all, rather than with a fabricated zero that nothing would ever come back and correct. The
`sum` aggregation silently skips exactly those documents, so what it returns is the **settled
remainder** of the window; `... or 0` then turns *nothing settled* into the claim that nothing was
spent, which is the one thing an absent figure never means.

Coverage is measurable in the same aggregation, which is why this is fixable at the read side without
touching what is written: a `value_count` on the money attribute counts the documents that carried
it, and a sibling bucket counts the cost-bearing documents that did not. Those two numbers decide the
whole qualification, and it is expressed as `RenderedSpend` - what `render_spend` produces for the
ledger-sourced surfaces - so a screen showing a money card has one spelling of absence to render
rather than one per data source.
"""

from __future__ import annotations

from datetime import datetime

from codemie.configs import config
from codemie.service.spend.coverage import LedgerCoverage, TimeRange
from codemie.service.spend.rendering import RenderedSpend

__all__ = [
    "SETTLED_AGG",
    "UNSETTLED_AGG",
    "money_coverage_aggs",
    "pre_floor_boundary",
    "read_money_coverage",
    "split_at_floor",
]

#: Sub-aggregation names, fixed here so the builder of a money bucket and its reader cannot drift.
SETTLED_AGG = "money_settled"
UNSETTLED_AGG = "money_unsettled"


def money_coverage_aggs(field: str, *, cost_bearing: dict | None = None) -> dict:
    """The two sub-aggregations that let a money `sum` say whether it is whole.

    `cost_bearing` narrows the unsettled count to the documents a money attribute is *expected* on,
    and is required wherever the sum's own scope is wider than the cost-bearing metrics: a
    `match_all` bucket also holds session, webhook and tool documents that never carry money, and
    counting those as unsettled would brand every total a subtotal in every window - an alarm that
    is always on is the same as no alarm.
    """
    unsettled: dict = {"must_not": [{"exists": {"field": field}}]}
    if cost_bearing is not None:
        unsettled["filter"] = [cost_bearing]
    return {
        SETTLED_AGG: {"value_count": {"field": field}},
        UNSETTLED_AGG: {"filter": {"bool": unsettled}},
    }


def read_money_coverage(bucket: dict, sum_key: str) -> RenderedSpend:
    """One money bucket - its sum and its two coverage counts - as a figure that carries its qualification.

    Three outcomes, and the middle one is the reason this exists:

    - **nothing settled** - no figure. `unavailable`, and never `0.0`: that no document in the window
      resolved a cost says nothing at all about whether money was spent.
    - **something unsettled** - a subtotal. The figure is published `partial`, with `missing_amount`
      unset, because the unsettled documents carry no money to add up: the size of the hole is
      genuinely unknown, which is a state and not a gap.
    - **everything settled** - the exact figure, unqualified. A *measured* `0.0` stays `0.0`.

    A coverage count missing from the response means coverage was never measured, which cannot
    certify a figure whole; that figure is published `partial` rather than bare, resolving the
    unknowable in the direction that surfaces it.

    Rounded to two places here rather than at each card, so the one figure a currency card shows is
    not rounded in three handlers three ways. **That rounding is for display, and a caller that also
    *filters* on the figure owns the difference:** a sub-cent sum rounds to an exact `0.0`, which a
    table dropping measured zeroes then drops a row on - see `project_handler._pre_floor_spend`, which
    keeps the unrounded sum for that one case rather than moving the rounding off every other card.
    """
    total = bucket.get(sum_key, {}).get("value")
    settled = bucket.get(SETTLED_AGG, {}).get("value")
    unsettled = bucket.get(UNSETTLED_AGG, {}).get("doc_count")

    if total is None or settled == 0:
        return RenderedSpend(figure=None, unavailable=True, partial=False, missing_amount=None)

    coverage_unmeasured = settled is None or unsettled is None
    return RenderedSpend(
        figure=round(float(total), 2),
        unavailable=False,
        partial=coverage_unmeasured or unsettled > 0,
        missing_amount=None,
    )


def pre_floor_boundary(coverage: LedgerCoverage) -> datetime | None:
    """The instant below which a surface serves its own Elasticsearch money, or None if it never does.

    **The floor is the registered one, handed in by the caller.** `ledger_coverage()` holds the value
    the composition root read once at startup; a surface re-deriving it from config would let the
    table and the ledger observe the setting mid-change and sit on opposite sides of one floor, which
    is the single failure the registry exists to prevent. The caller passes that registered value in
    rather than this module reading it, so a test can publish a floor at the one seam the surface is
    required to read. Unactivated coverage has no floor to split at, so the whole window takes the
    at-or-above shape - the behaviour every table had before any of this existed.

    **The kill switch is read from config, once per request, and that is safe for a reason worth
    stating.** `register_spend_cutover` reads the same setting at startup to decide whether a
    `LegacySpendLedger` sits under each registered ledger - but a table serving its pre-floor money
    from its own aggregation never asks the port below `F`, and at or above `F` a cutover-wrapped
    ledger and a bare one answer alike. Both directions of a mid-flight change stay honest: read true
    against a registry composed without the cutover, the pre-floor money never depended on the
    registration; read false against one that has it, the whole window goes to the port and the
    sub-`F` part comes back with no figure, so the row is labelled short rather than doubled or zeroed.

    One read per request, shared by the aggregation that scopes the money and the enricher that
    decides which legs run: two reads a request apart could disagree, leaving money clipped at a floor
    the split no longer believes in.
    """
    if not config.SPEND_LEDGER_LEGACY_BELOW_FLOOR or not coverage.activated:
        return None
    return coverage.coverage_floor


def split_at_floor(period: TimeRange, floor: datetime | None) -> tuple[TimeRange | None, TimeRange | None]:
    """`(below_floor, at_or_above_floor)` - either may be None, never both. No floor means no split.

    Arithmetic only: the *decision* of which floor to compare against is `pre_floor_boundary`'s, and
    a second floor read here would be the one the registry exists to prevent. `F` belongs to the
    at-or-above leg, because every interval on these paths is half-open and adjacent half-open ranges
    tile exactly - a closed lower leg `[start, F]` is the `BETWEEN` inclusivity that lands the boundary
    row in both legs, and its mirror `(F, end)` drops that row from both. Neither fails; both are
    wrong by one row. The comparisons mirror `CutoverSpendLedger._split` term for term, and must: a
    period a surface calls crossing but that ledger calls whole would be read in two shapes and
    answered by one engine.
    """
    if floor is None or floor <= period.start:
        return (None, period)
    if floor >= period.end:
        return (period, None)
    return (TimeRange(period.start, floor), TimeRange(floor, period.end))
