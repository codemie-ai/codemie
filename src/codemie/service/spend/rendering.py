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

"""One mapping from a `SpendResult` to what a surface renders.

**This module exists because four independent rewires each dropped the same two annotations.** Every
consumer took `SpendResult.figure`, rendered it, and discarded `unavailable` and `partial` - not from
carelessness but because a tabular row and a response model have a slot for a number and no slot for a
qualification. Three of the four reported it as an escalation in the same words: *a partial figure
renders indistinguishably from an exact one*, which is the looks-right-and-is-wrong outcome this whole
design exists to prevent. The port's own docstring already said it: *"a consumer that renders only
`figure` has dropped an annotation rather than simplified a call."*

So the mapping is defined once, here, and every surface emits the same three companion values beside
its number. What a *screen* does with them is a rendering decision and stays outside this module; what
it may not do is not receive them.

**The three states a surface must be able to tell apart, and why each pair is dangerous collapsed:**

| `figure` | `unavailable` | Means | Collapsed with the next row it would... |
|---|---|---|---|
| `None` | set | no figure exists for this range | claim nothing was spent |
| a number | set | a **truncated** total - one ledger's contribution is missing | present a subtotal as a total |
| a number | unset | the whole admitted range | - |

**And `partial` is not a third availability state.** Every row's money is complete whether or not it
carries a dimension key, so a dimension hole makes a figure *incomplete in that dimension* and never
unavailable. Letting it reach an availability decision would collide the two floors, which the
architecture refuses by name.

**`missing_amount is None` while `partial` is true is a real state, not a missing value.** The
identity-bridge portion has neither an amount nor a row count: an unresolvable alias set means the
person's fallback rows cannot be *identified*, so there is no row set to sum. A surface renders *an
unknown amount is missing* there - never a zero, never a blank, and never a row count standing in for
money.

**A residue is not per-row money, so a total over rows does not add the residues up.** A *grouped*
ledger read reads its residue once for the whole read and attaches the same `Portion` to every value's
`partial` - `aggregate_by_dimension` says so in as many words: *"the rows the union could not bucket
are missing from every value's figure, and any of them might belong to any of them, so no value may
claim it alone."* Twelve rows carrying that one portion therefore hold **one** hole between them, and
that is why each figure keeps the portions the port stated rather than only their sum: an amount alone
cannot tell one hole seen twelve times from twelve holes, and a combiner holding only the amounts can
do nothing but add them - which is how a $37.50 hole is reported as $450 against a $120 total.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal
from typing import cast
from datetime import datetime, timezone

from codemie.configs import logger
from codemie.service.spend.coverage import Portion, SpendDimension, SpendResult, TimeRange

__all__ = [
    "MissingPortion",
    "RenderedSpend",
    "apply_spend_cells",
    "combine_rendered_spend",
    "render_budget_spend",
    "render_spend",
    "spend_fields",
    "spend_footer",
    "report_period_or_none",
    "spend_row_fields",
    "time_range_or_none",
]

#: One dimension hole, carried past the render boundary with the identity the port gave it.
#:
#: The dimension **and** the portion together, because either half alone mis-identifies a hole: two
#: dimensions may be short by coincidentally equal amounts and are two holes, while one grouped read's
#: residue reaches every row of that read as the same dimension and the same `Portion` and is one.
MissingPortion = tuple[SpendDimension, Portion]

#: The keys every tabular money row carries beside its figure. Named once so four surfaces cannot
#: spell them three ways, which is the same failure as four surfaces computing the figure three ways.
FIELD_UNAVAILABLE = "spend_unavailable"
FIELD_PARTIAL = "spend_partial"
FIELD_MISSING = "spend_missing_amount"


@dataclass(frozen=True, slots=True)
class RenderedSpend:
    """A figure and the two things a surface must say about it.

    `figure` is a `float` because every response model on these paths is float-typed and has been
    since before this work; the exact `Decimal` never leaves the port, and no arithmetic happens
    here. `None` means **no figure**, and it is never a zero.
    """

    figure: float | None
    unavailable: bool
    partial: bool
    missing_amount: float | None
    #: The holes `missing_amount` is the size of, each still identified. **Not a duplicate of that
    #: amount:** it is what lets `combine_rendered_spend` recognise one shared hole arriving on N
    #: figures and count it once, which the amount alone cannot express. Empty is legitimate - a
    #: figure short by a whole absent *part* is partial and names no dimension hole at all.
    missing_portions: tuple[MissingPortion, ...] = ()

    @property
    def is_exact(self) -> bool:
        """A figure exists and nothing qualifies it. The only state safe to render bare."""
        return self.figure is not None and not self.unavailable and not self.partial

    def with_figure(self, figure: float) -> "RenderedSpend":
        """The same qualification around a different figure."""
        return RenderedSpend(
            figure=figure,
            unavailable=self.unavailable,
            partial=self.partial,
            missing_amount=self.missing_amount,
            missing_portions=self.missing_portions,
        )


def render_spend(result: SpendResult | None) -> RenderedSpend:
    """Turn a port answer - or its absence - into the four values a surface emits.

    `None` is accepted and means *the port was never asked*, which happens legitimately: a budget with
    no derivable period is not queried at all. It renders exactly like a no-figure answer, because to
    a reader the two are the same fact - no figure is available - and inventing a distinction would put
    a second unavailable state on the wire for no consumer to use.
    """
    if result is None:
        return RenderedSpend(figure=None, unavailable=True, partial=False, missing_amount=None)

    portions = list(result.partial.values())
    known = [portion.spend for portion in portions if portion.spend is not None]
    # Unknown wins: if any portion could not be computed, the missing total is not computable either,
    # and rendering only the computable half understates a hole whose size nobody knows.
    any_unknown = any(portion.spend is None for portion in portions)

    return RenderedSpend(
        figure=float(result.figure) if result.figure is not None else None,
        unavailable=result.unavailable is not None,
        partial=bool(portions),
        missing_amount=None if any_unknown or not known else float(sum(known)),
        missing_portions=tuple(result.partial.items()),
    )


def render_budget_spend(result: SpendResult | None) -> RenderedSpend:
    """`render_spend` for one budget's figure: partial in the project dimension, but never sized there.

    The project portion is the money below the identity floor that carries no project at all,
    platform-wide - *"a property of the period and not of a holder"*, as
    `SpendLogsLedger._project_portion` puts it. On one budget's figure that amount reads as the
    budget's own unattributed spend: EPMCDME-15237 showed *"(at least; $133,971.32 unattributed)"* on
    a project whose own figure was $0.01, and at a production rollout every budget's first period
    crosses the new floor. So the figure says it is partial without sizing the hole: the portion keeps
    its identity and loses its amount, which `combine_rendered_spend` already reads as unknown.
    The agent tool's `get_spending` figures are one scope's figures too and use it for the same reason
    (E2E pass H: a person's spend in a project created that day read "$136,040.59 unattributed").
    Analytics report tables keep sizing it through `render_spend`.

    Done here and not in the `SpendResult`, whose contract holds every portion but the identity bridge
    computable.
    """
    rendered = render_spend(result)
    if result is None or SpendDimension.PROJECT not in result.partial:
        return rendered
    unsized = tuple(
        (dimension, replace(portion, spend=None) if dimension is SpendDimension.PROJECT else portion)
        for dimension, portion in rendered.missing_portions
    )
    return cast(RenderedSpend, replace(rendered, missing_amount=None, missing_portions=unsized))


def render_composed_spend(result: SpendResult | None) -> RenderedSpend:
    """`render_budget_spend` for a composed scope's figure: partial in every dimension, never sized.

    Every residue `aggregate_composed` attaches - dimension, traffic class, project - is read across the
    whole platform rather than narrowed to the scope's user or project, so its size is other people's
    money. The figure still says it is partial; it never says by how much.
    """
    rendered = render_budget_spend(result)
    if not rendered.missing_portions and rendered.missing_amount is None:
        return rendered
    unsized = tuple(
        (dimension, replace(portion, spend=None, rows=None)) for dimension, portion in rendered.missing_portions
    )
    return cast(RenderedSpend, replace(rendered, missing_amount=None, missing_portions=unsized))


def spend_fields(rendered: RenderedSpend) -> dict[str, object]:
    """The three companion keys for a figure that is already rendered.

    Split out of `spend_row_fields` for the surfaces whose figure did not come from the spend port
    and so have no `SpendResult` to hand - an Elasticsearch money aggregation, for one. They must
    still emit the annotation under *these* three keys: a second spelling of absence is a second
    thing for every screen to fail to render, which is the failure this module exists to prevent.
    """
    return {
        FIELD_UNAVAILABLE: rendered.unavailable,
        FIELD_PARTIAL: rendered.partial,
        FIELD_MISSING: rendered.missing_amount,
    }


def spend_row_fields(result: SpendResult | None) -> dict[str, object]:
    """The three companion keys for a tabular row, ready to merge beside its figure.

    A `dict` rather than three positional values because the rows on these paths are dicts and a
    caller merging a mapping cannot transpose two booleans, which a caller passing three arguments can.
    """
    return spend_fields(render_spend(result))


def combine_rendered_spend(rendered: list[RenderedSpend]) -> RenderedSpend:
    """One figure over several, keeping every qualification the parts carried.

    Copied from the project router's assigned-budget total rather than invented, so that a project
    total means the same thing on both surfaces: a total short by one part is exactly a partial
    figure and says so, because otherwise it renders indistinguishably from a whole one.

    **The figures are added and the holes are not: a hole is counted once per hole, by value.** The
    figures come from disjoint row sets, so adding them is right. A residue does not: a grouped read
    reads it once and hands the same `Portion` to every value it returns, so the residue on twelve rows
    is one hole those twelve share, not twelve. Added, it reports the hole twelve times over - measured
    at $450 missing against a $120 total for a hole of $37.50, a figure so far past the total that the
    footer discredits the column it is meant to qualify.

    **Deduplicated, and specifically not collapsed.** `max()` or "take the first" would fix this row
    set and break the other one this function is handed: a per-row residue - one hole per figure, each
    its own - is genuinely additive and must still add up, and no rule that looks at one portion can
    tell the two apart. Value can: the same dimension with the same spend and the same rows is the
    same hole seen again, and the port's own `Portion` - a frozen dataclass - is already exactly that
    value.

    Two distinct holes of coincidentally equal size therefore count once, which is the deliberate half
    of the trade: over-reporting a hole discredits the figure beside it, while this understates by an
    amount only a collision produces. `partial` is unaffected either way - the figure still says it is
    short.
    """
    figures = [item.figure for item in rendered if item.figure is not None]
    partials = [item for item in rendered if item.partial]
    # Ordered dedup rather than a set: one row set then yields one tuple rather than one per hash seed.
    holes = tuple(dict.fromkeys(hole for item in partials for hole in item.missing_portions))
    known_missing = [portion.spend for _, portion in holes if portion.spend is not None]
    # Unknown wins, over the two ways a hole's contribution is not computable: a portion the port could
    # not compute at all, and a partial figure naming no portion - nothing there says whether its hole
    # is one of the identified ones or another, so it may be neither added to them nor dropped.
    any_unknown_missing = any(portion.spend is None for _, portion in holes) or any(
        not item.missing_portions for item in partials
    )

    return RenderedSpend(
        figure=float(sum(Decimal(str(figure)) for figure in figures)) if figures else None,
        unavailable=any(item.unavailable for item in rendered),
        partial=bool(partials) or (bool(figures) and any(item.figure is None for item in rendered)),
        # Summed in `Decimal` over the distinct holes and floated once, like every figure here.
        missing_amount=None if any_unknown_missing or not known_missing else float(sum(known_missing)),
        # Carried, so a combined figure combines again correctly: a footer over per-project totals that
        # each already share one period-scoped residue is that exact nesting.
        missing_portions=holes,
    )


def apply_spend_cells(rows: list[dict], rendered: list[RenderedSpend], column: str) -> None:
    """Write each row's money cell and the three values that qualify it.

    An absent figure stays absent - never 0, which would claim the holder spent nothing - and a
    measured zero stays a zero. The annotation is written in the same pass as the figure so a
    truncated or dimension-incomplete total cannot reach a screen looking like a whole one.
    """
    for row, spend in zip(rows, rendered, strict=True):
        row[column] = spend.figure
        row[FIELD_UNAVAILABLE] = spend.unavailable
        row[FIELD_PARTIAL] = spend.partial
        row[FIELD_MISSING] = spend.missing_amount


def spend_footer(column: str, rendered: list[RenderedSpend]) -> dict[str, object]:
    """The footer for one money column: its total, and the same triple the rows carry.

    The money key is **omitted** when no row produced a figure - already a legal footer shape, since
    an empty row set produces no key either - so a sum of nothing never presents itself as zero. The
    triple is keyed with the column id because one footer may qualify several columns, and it is
    what stops a total summed from only the rows that resolved from looking like a whole one.
    """
    if not rendered:
        return {}

    combined = combine_rendered_spend(rendered)
    # Sent unrounded like the row cells above it, so the client's one display rule applies to both:
    # rounded here, a column of sub-cent cells would total an exact 0.0 - real spend read as "$0.00"
    # (EPMCDME-15204).
    footer: dict[str, object] = {} if combined.figure is None else {column: combined.figure}
    footer[f"{column}_{FIELD_UNAVAILABLE}"] = combined.unavailable
    footer[f"{column}_{FIELD_PARTIAL}"] = combined.partial
    footer[f"{column}_{FIELD_MISSING}"] = combined.missing_amount
    return footer


def time_range_or_none(start_dt: datetime, end_dt: datetime) -> TimeRange | None:
    """The requested analytics window as the spend port's own range type, or None if it is not one.

    These tables answer "what was spent in this period", so the window is the report's own - never a
    budget's reset period, which answers the different question of how much of a limit is consumed.

    A naive datetime arrives whenever the caller sent a timestamp with no offset, and the port
    rejects those outright, so the missing offset is read as UTC like every other value on this path.
    A window the port cannot accept at all (start not before end) yields None: the port is then not
    asked, and every figure on the table is absent rather than a fabricated zero.
    """
    start = start_dt if start_dt.tzinfo else start_dt.replace(tzinfo=timezone.utc)
    end = end_dt if end_dt.tzinfo else end_dt.replace(tzinfo=timezone.utc)
    try:
        return TimeRange(start.astimezone(timezone.utc), end.astimezone(timezone.utc))
    except ValueError:
        logger.warning(f"Analytics window is not a readable period, so no spend is read for it: {start} .. {end}")
        return None


def report_period_or_none(start_dt: datetime, end_dt: datetime, client_source: str | None) -> TimeRange | None:
    """The window an analytics report asks the spend port about, or None when the port cannot answer it.

    Besides a window that is not one (`time_range_or_none`), a report narrowed to one client source is
    a question the port cannot answer: no ledger row records which client a call came from, so its
    figure would be the unnarrowed one, published under the narrowed report's label. The port is then
    not asked, and every money figure on the report is absent - never the whole platform's spend shown
    as one client's. Token and count columns keep the narrowing, which Elasticsearch applies.
    """
    if client_source:
        return None
    return time_range_or_none(start_dt, end_dt)
