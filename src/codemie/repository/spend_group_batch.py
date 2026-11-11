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

"""Many filters' summary-group figures, assigned from the one grouped read that answers them all."""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import ColumnElement

from codemie.repository.grouped_rows import GroupedRows, grouping_columns
from codemie.repository.spend_logs_repository import by_spend_group, report_filter_predicate
from codemie.service.spend.ledger import ReportFilter
from codemie.service.spend.spend_group import SpendGroup


@dataclass(frozen=True)
class SpendGroupBatch:
    """Many filters' summary-group figures, assigned in Python from one read grouped by what they name.

    Each filter keeps its own `report_filter_predicate`, evaluated over the grouped rows rather than
    written into the statement, so the statement is the same for one filter and for ten thousand.
    `group_key` is every column those predicates name. Both steps are pure, so they may run on a worker
    thread.
    """

    report_filters: tuple[ReportFilter, ...]
    predicates: tuple[ColumnElement[bool] | None, ...]
    group_key: tuple[str, ...]

    @classmethod
    def of(cls, report_filters: Collection[ReportFilter]) -> SpendGroupBatch:
        distinct = tuple(dict.fromkeys(report_filters))
        predicates = tuple(map(report_filter_predicate, distinct))
        return cls(distinct, predicates, grouping_columns(predicates))

    def figures(self, rows: Sequence[Sequence[object]]) -> dict[ReportFilter, dict[SpendGroup, Decimal]]:
        """Each filter's figures from rows of `sum_for_spend_groups_per_attribution`; a group's rows count in
        every filter that matches them, and a filter matching none is zero."""
        grouped = GroupedRows(rows, self.group_key)
        per_row = [by_spend_group(row[len(self.group_key) :]) for row in rows]
        return {
            report_filter: {
                group: sum((per_row[index][group] for index in grouped.matching(predicate)), Decimal("0"))
                for group in SpendGroup
            }
            for report_filter, predicate in zip(self.report_filters, self.predicates, strict=True)
        }
