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

"""The cost columns of the Analytics users-activity and projects-activity tables, joined around the ledger's CLI figure.

**CLI Cost comes from the spend ledger and the other three cost columns from the metric documents**, so
the row's Total Cost cannot be the metric documents' own total: that total carries the documents' CLI
money, and the row would show two figures for the same CLI spend. Total Cost is therefore the four
columns the row shows, combined by `combine_rendered_spend`, and the rows are ordered and footed by it.
It joins usage-metric web money with ledger CLI money, so a project's Total can differ from its
Projects Spending figure, which is the ledger's alone.

**A CLI cell with no figure is a dash, and the row's Total is then the amount of the parts it knows**,
shown bare by the product convention for a figure short by an unknown amount (EPMCDME-16846/16942). The
wire still says so: each of the two cells carries its own `<column>_spend_*` flags, as the routing tables'
money cells do, and a Total short its CLI part is flagged unavailable and partial. A measured zero is a
zero and adds nothing.

**This runs as the pipeline's row enricher, over every candidate row before the page is cut**, because a
column filled from outside Elasticsearch can be neither ordered nor totalled by Elasticsearch: filled on
the page afterwards, the footer summed empty cells and the order followed a Total the screen no longer
showed.
"""

from __future__ import annotations

from typing import Any

from codemie.service.analytics.query_pipeline import EnrichedRows
from codemie.service.spend.rendering import (
    RenderedSpend,
    column_money_cells,
    combine_rendered_spend,
    spend_footer,
)

CLI_COST_COLUMN = "cli_cost"
TOTAL_COST_COLUMN = "total_cost"

#: The cost columns the metric documents fill; CLI Cost is the fourth part of Total Cost.
METRIC_COST_COLUMNS = ("assistant_cost", "workflow_cost", "datasource_cost")


def _metric_part(amount: float) -> RenderedSpend:
    """A metric-document cost as a part of the Total: an Elasticsearch sum, always a figure and never qualified."""
    return RenderedSpend(figure=amount, unavailable=False, partial=False, missing_amount=None)


def activity_cost_rows(rows: list[dict[str, Any]], cli_spend: list[RenderedSpend]) -> EnrichedRows:
    """Fill each row's CLI Cost and Total Cost with their flags, order the rows by that Total, and foot both columns.

    `cli_spend` is each row's ledger CLI figure, in row order. Every row is kept: an activity row records
    activity, and a zero cost beside it is a fact about that activity. Both footers are `spend_footer`
    over the figures the cells show - CLI Cost's money key omitted when no row has a figure.
    """
    total_spend: list[RenderedSpend] = []
    for row, cli in zip(rows, cli_spend, strict=True):
        total = combine_rendered_spend([*(_metric_part(row[column]) for column in METRIC_COST_COLUMNS), cli])
        row.update(column_money_cells(CLI_COST_COLUMN, cli))
        row.update(column_money_cells(TOTAL_COST_COLUMN, total))
        total_spend.append(total)
    ordered = sorted(rows, key=lambda row: row[TOTAL_COST_COLUMN], reverse=True)
    totals = {**spend_footer(CLI_COST_COLUMN, cli_spend), **spend_footer(TOTAL_COST_COLUMN, total_spend)}
    return EnrichedRows(rows=ordered, columns=(CLI_COST_COLUMN, TOTAL_COST_COLUMN), totals=totals)
