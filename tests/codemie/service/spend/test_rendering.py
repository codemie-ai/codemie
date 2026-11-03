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

"""The render mapping, and the four collapses it refuses.

Each refusal is a pair of distinct facts that a surface would otherwise present identically. They are
tested as pairs rather than singly, because every one of these defects is invisible when only the
"good" side is exercised.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from codemie.service.spend.coverage import Portion, SpendDimension, SpendResult, TimeRange
from codemie.service.spend.rendering import (
    combine_rendered_spend,
    render_budget_spend,
    render_spend,
    report_period_or_none,
    spend_footer,
    spend_row_fields,
    time_range_or_none,
)

PERIOD = TimeRange(datetime(2026, 8, 1, tzinfo=timezone.utc), datetime(2026, 9, 1, tzinfo=timezone.utc))


class TestAvailability:
    def test_a_figure_with_nothing_qualifying_it_is_exact(self) -> None:
        rendered = render_spend(SpendResult(figure=Decimal("12.5")))
        assert rendered.figure == 12.5
        assert not rendered.unavailable and not rendered.partial
        assert rendered.is_exact

    def test_a_measured_zero_is_a_figure_and_stays_one(self) -> None:
        """The pair that matters: zero and absent must not render alike."""
        zero = render_spend(SpendResult(figure=Decimal("0")))
        absent = render_spend(SpendResult.no_figure(PERIOD))
        assert zero.figure == 0.0 and zero.is_exact
        assert absent.figure is None and absent.unavailable

    def test_a_truncated_total_carries_a_figure_and_the_flag(self) -> None:
        """A subtotal presented as a total is the defect; the flag is what stops it."""
        rendered = render_spend(SpendResult(figure=Decimal("9"), unavailable=PERIOD))
        assert rendered.figure == 9.0
        assert rendered.unavailable
        assert not rendered.is_exact

    def test_never_asked_renders_as_no_figure(self) -> None:
        """A budget with no derivable period is not queried; to a reader that is the same fact."""
        rendered = render_spend(None)
        assert rendered.figure is None and rendered.unavailable and not rendered.partial


class TestThePartialLabel:
    def test_a_dimension_hole_is_partial_and_never_unavailable(self) -> None:
        """Every row's money is complete; a dimension hole must not reach an availability decision."""
        result = SpendResult(
            figure=Decimal("100"),
            partial={SpendDimension.PROJECT: Portion(spend=Decimal("7"), rows=3)},
        )
        rendered = render_spend(result)
        assert rendered.partial and not rendered.unavailable
        assert rendered.missing_amount == 7.0

    def test_an_uncomputable_portion_reports_an_unknown_amount(self) -> None:
        """The identity bridge has neither an amount nor a row count - both are genuinely unknown."""
        result = SpendResult(
            figure=Decimal("100"),
            partial={SpendDimension.IDENTITY_BRIDGE: Portion(spend=None, rows=None)},
        )
        rendered = render_spend(result)
        assert rendered.partial
        assert rendered.missing_amount is None

    def test_an_unknown_portion_beats_a_known_one(self) -> None:
        """Rendering only the computable half understates a hole whose size nobody knows."""
        result = SpendResult(
            figure=Decimal("100"),
            partial={
                SpendDimension.PROJECT: Portion(spend=Decimal("7"), rows=3),
                SpendDimension.IDENTITY_BRIDGE: Portion(spend=None, rows=None),
            },
        )
        assert render_spend(result).missing_amount is None

    def test_two_known_portions_sum(self) -> None:
        result = SpendResult(
            figure=Decimal("100"),
            partial={
                SpendDimension.PROJECT: Portion(spend=Decimal("7"), rows=3),
                SpendDimension.WORKFLOW: Portion(spend=Decimal("2.5"), rows=1),
            },
        )
        assert render_spend(result).missing_amount == 9.5


class TestTheRowFields:
    def test_the_three_keys_are_always_present(self) -> None:
        """A key a surface sometimes omits is a key no consumer can rely on."""
        for result in (None, SpendResult.no_figure(PERIOD), SpendResult(figure=Decimal("1"))):
            fields = spend_row_fields(result)
            assert set(fields) == {"spend_unavailable", "spend_partial", "spend_missing_amount"}

    def test_the_flags_are_real_booleans_and_not_the_underlying_objects(self) -> None:
        """A TimeRange in a boolean slot is truthy and serialises as an object nobody expects."""
        fields = spend_row_fields(SpendResult(figure=Decimal("1"), unavailable=PERIOD))
        assert fields["spend_unavailable"] is True
        assert fields["spend_partial"] is False


class TestTheFooter:
    def test_a_sub_cent_total_is_not_rounded_to_zero(self) -> None:
        """Rounded here, cells reading $0.002 each would total an exact 0.0 - real spend shown as $0.00."""
        rendered = [render_spend(SpendResult(figure=Decimal("0.002"))) for _ in range(2)]

        footer = spend_footer("total_cost_usd", rendered)

        assert footer["total_cost_usd"] == pytest.approx(0.004)


class TestCombiningFigures:
    """One hole shared by many rows against many rows each holding their own - the pair that must
    not be treated alike.

    A *grouped* ledger read reads its residue once and attaches the same `Portion` to every value it
    returns, while a per-row residue gives each figure a hole of its own. The two row sets are
    indistinguishable from the amounts alone, so they are tested as a pair: adding is right for one
    and multiplies the other by the row count, and collapsing with `max()` or the first portion is
    right for one and silently loses the other.
    """

    def test_one_residue_shared_by_every_row_is_counted_once(self) -> None:
        """Added instead, a $37.50 hole is reported as $450 missing against a $120 total."""
        shared = Portion(spend=Decimal("37.50"), rows=1200)
        rendered = [
            render_spend(SpendResult(figure=Decimal("10.00"), partial={SpendDimension.WORKFLOW: shared}))
            for _ in range(12)
        ]

        footer = spend_footer("total_cost_usd", rendered)

        assert footer["total_cost_usd"] == 120.0
        # The amount is corrected and the qualification is kept: a hole counted once is still a hole.
        assert footer["total_cost_usd_spend_partial"] is True
        assert footer["total_cost_usd_spend_missing_amount"] == 37.5

    def test_a_residue_that_is_genuinely_per_row_still_sums(self) -> None:
        """Three holes of equal size are three holes - what a `max()` or first-portion fix loses.

        Equal amounts on purpose: the row count is part of a hole's identity, so figures short by the
        same money over different unbucketed row sets are distinct holes and must still add up.
        """
        rendered = [
            render_spend(
                SpendResult(
                    figure=Decimal("10.00"),
                    partial={SpendDimension.WORKFLOW: Portion(spend=Decimal("1.50"), rows=rows)},
                )
            )
            for rows in (1, 2, 3)
        ]

        combined = combine_rendered_spend(rendered)

        assert combined.figure == 30.0
        assert combined.partial
        assert combined.missing_amount == 4.5

    def test_two_dimensions_short_by_the_same_amount_are_two_holes(self) -> None:
        """A hole is identified by its dimension as well as its portion: short twice is short twice."""
        result = SpendResult(
            figure=Decimal("100"),
            partial={
                SpendDimension.PROJECT: Portion(spend=Decimal("7"), rows=3),
                SpendDimension.WORKFLOW: Portion(spend=Decimal("7"), rows=3),
            },
        )
        assert combine_rendered_spend([render_spend(result)]).missing_amount == 14.0

    def test_a_shared_residue_stays_one_hole_through_a_second_combine(self) -> None:
        """The per-project path is exactly this nesting: totals are combined, then a footer combines those."""
        shared = Portion(spend=Decimal("37.50"), rows=1200)
        per_project = [
            combine_rendered_spend(
                [render_spend(SpendResult(figure=Decimal("10.00"), partial={SpendDimension.PROJECT: shared}))]
            )
            for _ in range(4)
        ]

        footer = spend_footer("total_cost_usd", per_project)

        assert footer["total_cost_usd"] == 40.0
        assert footer["total_cost_usd_spend_missing_amount"] == 37.5

    def test_a_shared_hole_nobody_can_size_stays_unsized(self) -> None:
        """Deduplication must not turn an unknown amount into a known one - a zero here is a lie."""
        unknown = Portion(spend=None, rows=None)
        rendered = [
            render_spend(SpendResult(figure=Decimal("5"), partial={SpendDimension.IDENTITY_BRIDGE: unknown}))
            for _ in range(3)
        ]

        combined = combine_rendered_spend(rendered)

        assert combined.figure == 15.0
        assert combined.partial
        assert combined.missing_amount is None


class TestABudgetFigureNeverSizesTheProjectHole:
    """The project hole is platform-wide, so on one budget it would read as that budget's own spend.

    EPMCDME-15237: *"(at least; $133,971.32 unattributed)"* on a project whose own figure was $0.01.
    """

    PROJECT_HOLE = Portion(spend=Decimal("133971.32"), rows=5000, sub_range=PERIOD)

    def test_the_hole_is_reported_but_not_sized(self) -> None:
        result = SpendResult(figure=Decimal("0.01"), partial={SpendDimension.PROJECT: self.PROJECT_HOLE})
        sized = render_spend(result)

        rendered = render_budget_spend(result)

        assert rendered.partial and rendered.missing_amount is None
        assert (rendered.figure, rendered.unavailable) == (sized.figure, sized.unavailable)
        ((dimension, portion),) = rendered.missing_portions
        assert dimension is SpendDimension.PROJECT
        assert portion == Portion(spend=None, rows=5000, sub_range=PERIOD)

    @pytest.mark.parametrize(
        "result",
        [
            None,
            SpendResult(figure=Decimal("12.5")),
            SpendResult(figure=Decimal("5"), partial={SpendDimension.IDENTITY_BRIDGE: Portion(spend=None, rows=None)}),
        ],
        ids=["never-asked", "exact", "identity-bridge-hole"],
    )
    def test_without_a_project_hole_it_is_the_plain_rendering(self, result) -> None:
        assert render_budget_spend(result) == render_spend(result)

    def test_a_sized_hole_beside_it_does_not_size_the_figure(self) -> None:
        """Rendering only the sized dimension would present the workflow hole as the whole of it."""
        result = SpendResult(
            figure=Decimal("100"),
            partial={
                SpendDimension.PROJECT: self.PROJECT_HOLE,
                SpendDimension.WORKFLOW: Portion(spend=Decimal("2.5"), rows=1),
            },
        )
        assert render_budget_spend(result).missing_amount is None

    def test_budgets_sharing_the_hole_combine_to_one_unsized_hole(self) -> None:
        """A project total over its budgets must not size the hole either, nor count it twice."""
        rendered = [
            render_budget_spend(
                SpendResult(figure=Decimal(figure), partial={SpendDimension.PROJECT: self.PROJECT_HOLE})
            )
            for figure in ("0.01", "0.02")
        ]

        combined = combine_rendered_spend(rendered)

        assert combined.partial and combined.missing_amount is None
        assert len(combined.missing_portions) == 1


class TestTheReportPeriod:
    """The window a report asks the port about, or None - which leaves every figure on it absent."""

    def test_a_client_source_filter_yields_no_period(self) -> None:
        """No ledger row records a client, so the port's figure would be the unnarrowed one under the
        narrowed report's label."""
        assert report_period_or_none(PERIOD.start, PERIOD.end, "teams") is None

    @pytest.mark.parametrize(
        ("start", "end", "expected"),
        [
            (PERIOD.start, PERIOD.end, PERIOD),
            # The port rejects an offset-less instant outright, so the missing offset is read as UTC.
            (datetime(2026, 8, 1), datetime(2026, 9, 1), PERIOD),
            # Not a period at all: the port is not asked rather than answered with a zero.
            (PERIOD.end, PERIOD.start, None),
        ],
        ids=["aware", "naive-read-as-utc", "inverted"],
    )
    def test_without_one_it_is_the_plain_window_rule(self, start, end, expected) -> None:
        assert report_period_or_none(start, end, None) == expected == time_range_or_none(start, end)
