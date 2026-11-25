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

"""What more than one spend widget shares. Used inside this package only; a widget hands out ready data."""

from collections.abc import Sequence
from typing import Any

from codemie.service.analytics.handlers.ledger_group_spend import TableFilters
from codemie.service.spend.coverage import TimeRange
from codemie.service.spend.ledger import HOLDER_BATCH_SIZE, AccessScope, ReportFilter
from codemie.service.spend.rendering import render_spend
from codemie.service.spend.spend_group import SpendGroup
from codemie.service.spend.spend_service import spend_service
from codemie.service.usage_attribution.spend_widgets.base import SpendFigures, SpendTable


def round_cents(x: float | None) -> float | None:
    """A figure rounded to cents; no figure stays None, never a zero."""
    return None if x is None else round(float(x), 2)


def rendered(answer: dict[SpendGroup, Any]) -> SpendFigures:
    """The bare figures of a port answer, in cents; ``complete`` is False when any of them was unavailable."""
    spends = {group: render_spend(answer.get(group)) for group in SpendGroup}
    return SpendFigures.from_groups(
        {group: round_cents(spend.figure) for group, spend in spends.items()},
        complete=not any(spend.unavailable for spend in spends.values()),
    )


def no_rows(keys: Sequence[str]) -> SpendTable:
    """A table in which every key has no figures."""
    return SpendTable({key: SpendFigures() for key in keys})


async def read_per_row(
    window: TimeRange,
    access: AccessScope,
    keys: Sequence[str],
    filters: TableFilters,
    *,
    batch_size: int = HOLDER_BATCH_SIZE,
) -> SpendTable:
    """Read the named filters in batches; a row naming nobody gets no figure."""
    named = list(dict.fromkeys(f for f in filters.rows if f is not None))
    answers: dict[ReportFilter, SpendFigures] = {}

    for i in range(0, len(named), batch_size):
        chunk = named[i : i + batch_size]
        batch = await spend_service.aggregate_by_spend_group_per_filter(
            window, access=access, common_filter=filters.common, report_filters=chunk
        )
        for f in chunk:
            answers[f] = rendered(batch.get(f) or {})

    rows = {
        key: answers[f] if f is not None and f in answers else SpendFigures()
        for key, f in zip(keys, filters.rows, strict=True)
    }

    return SpendTable(rows, complete=all(figures.complete for figures in answers.values()))
