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

"""The two floors, their fixed order, and the invariants the result shape must make unrepresentable."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from codemie.service.spend.coverage import (
    LedgerCoverage,
    Portion,
    SpendDimension,
    SpendResult,
    TimeRange,
)


def utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


F = utc(2026, 8, 1)
E = utc(2026, 8, 15)


@pytest.fixture
def activated() -> LedgerCoverage:
    return LedgerCoverage(coverage_floor=F, explicit_identity_floor=E, ready=True)


class TestTimeRangeIsHalfOpen:
    def test_rejects_an_inverted_or_empty_range(self):
        with pytest.raises(ValueError, match="non-empty and forward"):
            TimeRange(utc(2026, 8, 2), utc(2026, 8, 1))
        with pytest.raises(ValueError, match="non-empty and forward"):
            TimeRange(F, F)

    def test_rejects_a_naive_datetime(self):
        """UTC is enforced by the type rather than by every caller remembering it."""
        with pytest.raises(ValueError, match="must be UTC-aware"):
            TimeRange(datetime(2026, 8, 1), datetime(2026, 8, 2))

    def test_rejects_a_non_utc_offset(self):
        with pytest.raises(ValueError, match="must be UTC-aware"):
            TimeRange(
                datetime(2026, 8, 1, tzinfo=timezone(timedelta(hours=2))),
                datetime(2026, 8, 2, tzinfo=timezone.utc),
            )

    def test_adjacent_ranges_tile_exactly(self):
        """No row counted twice, none lost - which a closed upper bound would break."""
        july = TimeRange(utc(2026, 7, 1), utc(2026, 8, 1))
        august = TimeRange(utc(2026, 8, 1), utc(2026, 9, 1))
        assert july.end == august.start

    def test_widened_pushes_both_ends_out(self):
        widened = TimeRange(F, E).widened(timedelta(hours=24))
        assert widened.start == F - timedelta(hours=24)
        assert widened.end == E + timedelta(hours=24)


class TestEvaluationOrder:
    """The spine's worked example, because the wrong order is the plausible one."""

    def test_a_range_crossing_both_floors_yields_one_figure_with_both_annotations(self, activated):
        requested = TimeRange(utc(2026, 7, 15), utc(2026, 8, 31))

        admitted = activated.admitted(requested)
        flag = activated.unavailable_flag(requested)
        sub_e = activated.project_dimension_portion(admitted)

        assert admitted == TimeRange(F, utc(2026, 8, 31))
        assert flag == TimeRange(utc(2026, 7, 15), F)
        assert sub_e == TimeRange(F, E)

    def test_the_sub_f_portion_is_never_labelled(self, activated):
        """`E` is applied to the portion `F` admitted and to nothing else.

        Labelling nothing is not a weaker claim than unavailable; it is a different, false one.
        """
        requested = TimeRange(utc(2026, 7, 15), utc(2026, 8, 31))
        sub_e = activated.project_dimension_portion(activated.admitted(requested))
        assert sub_e.start == F  # not 2026-07-15


class TestCoverageFloorDecidesExistence:
    def test_a_range_wholly_below_the_floor_admits_nothing(self, activated):
        assert activated.admitted(TimeRange(utc(2026, 6, 1), utc(2026, 7, 1))) is None

    def test_a_row_exactly_at_the_floor_belongs_to_the_ledger(self, activated):
        assert activated.admitted(TimeRange(F, utc(2026, 8, 2))) == TimeRange(F, utc(2026, 8, 2))

    def test_an_unactivated_ledger_admits_nothing_anywhere(self):
        """Before a first activation the ledger answers nothing, id-keyed reads included."""
        unpublished = LedgerCoverage(coverage_floor=None, ready=True)
        assert unpublished.activated is False
        assert unpublished.admitted(TimeRange(F, E)) is None

    def test_invalidated_readiness_admits_nothing_even_above_the_floor(self):
        invalidated = LedgerCoverage(coverage_floor=F, ready=False)
        assert invalidated.admitted(TimeRange(utc(2026, 8, 10), utc(2026, 8, 20))) is None


class TestUnavailableFlagHasTwoCauses:
    def test_floor_cause_names_only_the_uncovered_portion(self, activated):
        requested = TimeRange(utc(2026, 7, 15), utc(2026, 8, 31))
        assert activated.unavailable_flag(requested) == TimeRange(utc(2026, 7, 15), F)

    def test_a_range_wholly_above_the_floor_carries_no_flag(self, activated):
        assert activated.unavailable_flag(TimeRange(utc(2026, 8, 10), utc(2026, 8, 20))) is None

    def test_readiness_cause_names_the_whole_range_even_when_it_starts_above_the_floor(self):
        """The trap: the floor formula computes an *empty* interval here.

        A result carrying `figure is None` would then carry a flag naming no time at all,
        which is the opposite of what the shape requires.
        """
        invalidated = LedgerCoverage(coverage_floor=F, ready=False)
        requested = TimeRange(utc(2026, 8, 10), utc(2026, 8, 20))
        assert invalidated.unavailable_flag(requested) == requested


class TestExplicitIdentityFloorOnlyLabels:
    def test_an_unset_floor_puts_every_period_below_it(self):
        """A correct steady state for a deployment that never rolls explicit identity out."""
        no_e = LedgerCoverage(coverage_floor=F, explicit_identity_floor=None, ready=True)
        admitted = TimeRange(F, utc(2026, 8, 31))
        assert no_e.project_dimension_portion(admitted) == admitted

    def test_a_range_wholly_above_the_floor_is_not_labelled(self, activated):
        assert activated.project_dimension_portion(TimeRange(utc(2026, 8, 20), utc(2026, 8, 31))) is None

    def test_a_floor_raised_past_the_identity_floor_is_benign(self):
        """A retention republication raises `F` deliberately and may raise it past `E`.

        Every admitted row then carries explicit identity and `E` labels nothing. An
        implementation asserting `E >= F` on every read would fire on this recovery path.
        """
        republished = LedgerCoverage(coverage_floor=utc(2026, 9, 1), explicit_identity_floor=E, ready=True)
        assert republished.project_dimension_portion(TimeRange(utc(2026, 9, 1), utc(2026, 10, 1))) is None


class TestSpendResultMakesTheFabricatedZeroUnrepresentable:
    def test_no_figure_names_the_whole_request_and_carries_no_label(self):
        requested = TimeRange(utc(2026, 6, 1), utc(2026, 7, 1))
        result = SpendResult.no_figure(requested)
        assert result.figure is None
        assert result.unavailable == requested
        assert not result.partial

    def test_a_measured_zero_is_a_real_figure(self):
        """A measured zero and an absent figure are different facts."""
        assert SpendResult(figure=Decimal("0")).figure == Decimal("0")
        assert SpendResult(figure=Decimal("0")).is_exact

    def test_an_absent_figure_requires_a_flag(self):
        with pytest.raises(ValueError, match="requires an unavailable range"):
            SpendResult(figure=None)

    def test_an_absent_figure_cannot_be_partial(self):
        """No portion was admitted, so there is nothing to be incomplete about."""
        requested = TimeRange(F, E)
        with pytest.raises(ValueError, match="admits no portion"):
            SpendResult(
                figure=None,
                unavailable=requested,
                partial={SpendDimension.PROJECT: Portion(Decimal("1"), 1)},
            )


class TestPartialIsASetOfPerDimensionEntries:
    def test_a_figure_can_be_partial_in_two_dimensions_with_two_different_portions(self):
        """One slot silently drops one of them."""
        result = SpendResult(
            figure=Decimal("100"),
            partial={
                SpendDimension.PROJECT: Portion(Decimal("3.10"), 1_200, TimeRange(F, E)),
                SpendDimension.WORKFLOW: Portion(Decimal("0.40"), 90_000),
            },
        )
        assert len(result.partial) == 2
        assert result.partial[SpendDimension.PROJECT].sub_range == TimeRange(F, E)
        assert result.partial[SpendDimension.WORKFLOW].sub_range is None

    def test_both_annotations_may_be_present_and_neither_suppresses_the_other(self, activated):
        requested = TimeRange(utc(2026, 7, 15), utc(2026, 8, 31))
        admitted = activated.admitted(requested)
        result = SpendResult(
            figure=Decimal("412.50"),
            unavailable=activated.unavailable_flag(requested),
            partial={
                SpendDimension.PROJECT: Portion(Decimal("3.10"), 1_200, activated.project_dimension_portion(admitted))
            },
        )
        assert result.unavailable is not None
        assert result.partial
        assert not result.is_exact

    def test_identity_bridge_is_the_one_dimension_whose_portion_may_be_uncomputable(self):
        """An unresolvable alias set means the rows cannot be identified, so none can be summed."""
        result = SpendResult(
            figure=Decimal("100"),
            partial={SpendDimension.IDENTITY_BRIDGE: Portion(None, None)},
        )
        assert result.partial[SpendDimension.IDENTITY_BRIDGE].spend is None

    def test_an_uncomputable_portion_on_any_other_dimension_is_a_defect(self):
        with pytest.raises(ValueError, match="only identity_bridge may be unknown"):
            SpendResult(figure=Decimal("100"), partial={SpendDimension.WORKFLOW: Portion(None, None)})

    def test_only_a_published_floor_scopes_a_portion_as_an_interval(self):
        """Manufacturing a floor for a dimension that has none is the refused third floor."""
        with pytest.raises(ValueError, match="no published floor"):
            SpendResult(
                figure=Decimal("100"),
                partial={SpendDimension.WORKFLOW: Portion(Decimal("1"), 1, TimeRange(F, E))},
            )

    def test_the_partial_mapping_is_frozen(self):
        result = SpendResult(figure=Decimal("100"))
        with pytest.raises(TypeError):
            result.partial[SpendDimension.PROJECT] = Portion(Decimal("1"), 1)


class TestConfigIsReadOnceWithPublishedDefaults:
    def test_defaults_are_the_published_values(self):
        coverage = LedgerCoverage()
        assert coverage.emission_lag == timedelta(hours=24)
        assert coverage.max_elasticsearch_ids == 50_000

    def test_an_unactivated_default_answers_nothing(self):
        assert LedgerCoverage().activated is False
