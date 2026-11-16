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

"""CLI ledger money survives classification, cache round-trips and unavailable reads."""

from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from codemie.service.spend.coverage import TimeRange
from codemie.service.spend.ledger import CliSpendRow
from codemie.service.usage_attribution.settings import build_defaults
from codemie.service.usage_attribution.spend_widgets import CliUsageFigures, CliUsageSpend
from codemie.service.usage_attribution.spend_widgets.base import CliCategorySpend

from .conftest import END, FLOOR, SCOPE, START, FakeCache, context, coverage


@pytest.fixture
def evidence():
    return {
        "persons": {
            "u": {
                "name": "user",
                "spend": 999.0,
                "cb": 0.0,
                "sh": 0.0,
                "nc": 0.0,
                "pairs": {"": [999.0, 0.0, 0.0, 0.0]},
                "projects": {},
                "fallback": {},
            }
        },
    }


@pytest.mark.asyncio
async def test_cached_cli_categories_use_ledger_money_and_keep_missing_repository_spend(port, evidence):
    port.cli_attribution_rows = AsyncMock(
        return_value=(
            CliSpendRow(
                user_id="u",
                principal=None,
                project=None,
                form="principal",
                repository=None,
                branch=None,
                client="codemie-claude",
                spend=Decimal("6.25"),
            ),
        )
    )
    cache = FakeCache()
    widget = CliUsageSpend(context(cache), evidence=evidence, settings=build_defaults({}).values)
    with coverage():
        first = await widget.read(START, END, "analysis:v1")
        cached = await widget.read(START, END, "analysis:v1")
    assert first == cached
    assert cached.total == cached.unclassified == 6.25
    assert sum(row.spend for row in cached.categories) == 6.25
    assert all(isinstance(row, CliCategorySpend) for row in cached.categories)
    assert evidence["persons"]["u"]["spend"] == 999.0
    port.cli_attribution_rows.assert_awaited_once_with(TimeRange(FLOOR, END), access=SCOPE)
    assert len(cache.store) == 1


@pytest.mark.asyncio
async def test_an_unavailable_cli_read_has_no_figures_and_is_retried(port, evidence):
    port.cli_attribution_rows = AsyncMock(return_value=None)
    cache = FakeCache()
    widget = CliUsageSpend(context(cache), evidence=evidence, settings=build_defaults({}).values)
    with coverage():
        first = await widget.read(START, END, "analysis:v1")
        second = await widget.read(START, END, "analysis:v1")
    assert first == second == CliUsageFigures(complete=False)
    assert not first.complete
    assert first.total is None and first.unclassified is None
    assert port.cli_attribution_rows.await_count == 2
    assert cache.store == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("kwargs", [{"floor": END}, {"ready": False}])
async def test_no_coverage_does_not_read_cli_spend(port, evidence, kwargs):
    port.cli_attribution_rows = AsyncMock()
    widget = CliUsageSpend(context(), evidence=evidence, settings=build_defaults({}).values)
    with coverage(**kwargs):
        result = await widget.read(START, END, "analysis:v1")
    assert result == CliUsageFigures()
    port.cli_attribution_rows.assert_not_awaited()
