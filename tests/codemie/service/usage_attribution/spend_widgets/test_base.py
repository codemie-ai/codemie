# Copyright 2026 EPAM Systems, Inc. (“EPAM”)
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

"""The typed outputs of a spend widget, the caller's context, and the coverage-floor window rule."""

from unittest.mock import MagicMock, patch

import pytest
from pydantic import TypeAdapter, ValidationError

from codemie.service.spend.coverage import TimeRange
from codemie.service.spend.spend_group import SpendGroup
from codemie.service.usage_attribution.spend_widgets import SpendFigures, SpendTable, SpendWidget, WidgetContext, base

from .conftest import END, FLOOR, SCOPE, START, USER, coverage


def test_every_spend_group_has_a_field():
    assert {group.value for group in SpendGroup} <= set(SpendFigures.__dataclass_fields__)


def test_no_figures_is_none_never_zero():
    assert SpendFigures() == SpendFigures(None, None, None, None)
    assert SpendFigures().total is None


def test_figures_from_groups_and_back():
    figures = SpendFigures.from_groups({SpendGroup.TOTAL: 10.0, SpendGroup.CLI: 6.0})
    assert (figures.total, figures.platform, figures.cli, figures.embedding) == (10.0, None, 6.0, None)
    assert figures.of(SpendGroup.CLI) == 6.0 and figures.of(SpendGroup.EMBEDDING) is None


def test_completeness_takes_no_part_in_equality():
    assert SpendFigures(total=1.0, complete=False) == SpendFigures(total=1.0)


def test_figures_are_frozen():
    with pytest.raises((AttributeError, ValidationError, TypeError)):
        SpendFigures().total = 1.0  # type: ignore[misc]


def test_a_missing_key_has_no_figures():
    assert SpendTable({"a": SpendFigures(total=1.0)}).of("b") == SpendFigures()
    assert SpendTable().of("a") == SpendFigures()


def test_a_table_survives_a_json_round_trip():
    table = SpendTable({"a": SpendFigures(total=1.0, cli=0.5), "b": SpendFigures()})
    adapter = TypeAdapter(SpendTable)
    assert adapter.validate_json(adapter.dump_json(table)) == table


class TestWidgetContext:
    def test_the_access_scope_is_derived_once(self):
        pipeline = MagicMock()
        pipeline.return_value.access_scope = SCOPE
        with patch.object(base, "AnalyticsQueryPipeline", pipeline), patch.object(base, "MetricsElasticRepository"):
            context = WidgetContext(USER, MagicMock())
            assert context.scope is SCOPE and context.scope is SCOPE
        assert pipeline.call_count == 1

    def test_an_explicit_access_scope_is_used_as_given(self):
        pipeline = MagicMock()
        with patch.object(base, "AnalyticsQueryPipeline", pipeline):
            assert WidgetContext(USER, MagicMock(), SCOPE).scope is SCOPE
        pipeline.assert_not_called()


class TestTheWindow:
    def test_a_window_crossing_the_floor_is_clipped_to_it(self):
        with coverage():
            assert SpendWidget._window(START, END) == TimeRange(FLOOR, END)

    def test_a_window_wholly_below_the_floor_has_no_window(self):
        with coverage(floor=END):
            assert SpendWidget._window(START, END) is None

    def test_a_ledger_that_is_not_activated_has_no_window(self):
        with coverage(ready=False):
            assert SpendWidget._window(START, END) is None

    @pytest.mark.parametrize("legacy", [True, False])
    def test_the_legacy_below_floor_switch_does_not_widen_the_window(self, legacy):
        cfg = MagicMock(SPEND_LEDGER_LEGACY_BELOW_FLOOR=legacy)
        with (
            coverage(),
            patch("codemie.configs.config.config", cfg),
            patch(
                "codemie.service.analytics.handlers.money_coverage.pre_floor_boundary",
                side_effect=AssertionError("pre_floor_boundary must not be used"),
            ),
        ):
            assert SpendWidget._window(START, END) == TimeRange(FLOOR, END)
