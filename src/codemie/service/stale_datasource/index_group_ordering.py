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

"""Ordering of stale datasource rows grouped by their Elasticsearch index."""

from __future__ import annotations

from datetime import date, datetime, UTC

from codemie.rest_api.models.index import IndexInfo


def _naive_utc(value: datetime) -> datetime:
    if value.tzinfo is not None:
        return value.astimezone(UTC).replace(tzinfo=None)
    return value


def _none_first(value: date | None) -> tuple[int, date | None]:
    return (0, None) if value is None else (1, value)


def _row_order_key(row: IndexInfo) -> tuple[tuple[int, date | None], tuple[int, datetime | None], str]:
    marked = row.marked_stale_at
    stale_day = _naive_utc(marked).date() if marked is not None else None
    last_update = row.update_date if row.update_date is not None else row.date
    if last_update is not None:
        last_update = _naive_utc(last_update)
    return _none_first(stale_day), _none_first(last_update), row.id


def order_index_groups(rows_by_index: dict[str, list[IndexInfo]]) -> list[tuple[str, list[str]]]:
    keyed = [(min(_row_order_key(row) for row in rows), name, rows) for name, rows in rows_by_index.items()]
    keyed.sort(key=lambda item: (item[0], item[1]))
    return [(name, [row.id for row in rows]) for _, name, rows in keyed]
