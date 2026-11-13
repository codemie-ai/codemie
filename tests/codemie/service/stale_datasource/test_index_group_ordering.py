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

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from codemie.service.stale_datasource.index_group_ordering import order_index_groups


class TestOrderIndexGroups:
    @staticmethod
    def _row(row_id, marked=None, update=None, date=None):
        row = MagicMock()
        row.id = row_id
        row.marked_stale_at = marked
        row.update_date = update
        row.date = date
        return row

    @staticmethod
    def _names(groups):
        return [name for name, _ in groups]

    def test_earlier_stale_mark_day_first(self):
        late = self._row("a", marked=datetime(2026, 3, 2, 1))
        early = self._row("b", marked=datetime(2026, 3, 1, 23))

        groups = order_index_groups({"late": [late], "early": [early]})

        assert self._names(groups) == ["early", "late"]

    def test_same_day_ordered_by_update_date(self):
        day = datetime(2026, 3, 1, 12)
        newer = self._row("a", marked=day, update=datetime(2026, 2, 2))
        older = self._row("b", marked=day, update=datetime(2026, 1, 1))

        groups = order_index_groups({"newer": [newer], "older": [older]})

        assert self._names(groups) == ["older", "newer"]

    def test_update_date_none_falls_back_to_date(self):
        day = datetime(2026, 3, 1, 12)
        by_date = self._row("a", marked=day, update=None, date=datetime(2026, 1, 1))
        by_update = self._row("b", marked=day, update=datetime(2026, 2, 1), date=datetime(2025, 1, 1))

        groups = order_index_groups({"by_update": [by_update], "by_date": [by_date]})

        assert self._names(groups) == ["by_date", "by_update"]

    def test_marked_stale_at_none_sorts_first(self):
        unmarked = self._row("a", marked=None, update=datetime(2026, 5, 1))
        marked = self._row("b", marked=datetime(2020, 1, 1), update=datetime(2020, 1, 1))

        groups = order_index_groups({"marked": [marked], "unmarked": [unmarked]})

        assert self._names(groups) == ["unmarked", "marked"]

    def test_all_dates_none_sorts_first_within_a_day(self):
        day = datetime(2026, 3, 1, 12)
        dated = self._row("a", marked=day, update=datetime(2020, 1, 1))
        undated = self._row("b", marked=day)

        groups = order_index_groups({"dated": [dated], "undated": [undated]})

        assert self._names(groups) == ["undated", "dated"]

    def test_all_fields_none_does_not_crash(self):
        groups = order_index_groups({"x": [self._row("a")], "y": [self._row("b")]})

        assert self._names(groups) == ["x", "y"]

    def test_group_positioned_by_its_earliest_row(self):
        mixed = [
            self._row("a", marked=datetime(2026, 3, 5)),
            self._row("b", marked=datetime(2026, 3, 1)),
        ]
        middle = self._row("c", marked=datetime(2026, 3, 3))

        groups = order_index_groups({"middle": [middle], "mixed": mixed})

        assert self._names(groups) == ["mixed", "middle"]

    def test_id_breaks_ties(self):
        day = datetime(2026, 3, 1)
        first = self._row("a", marked=day, update=datetime(2026, 1, 1))
        second = self._row("b", marked=day, update=datetime(2026, 1, 1))

        groups = order_index_groups({"second": [second], "first": [first]})

        assert self._names(groups) == ["first", "second"]

    def test_timezone_aware_marked_stale_at_converted_to_utc_date(self):
        plus_five = timezone(timedelta(hours=5))
        aware = self._row("a", marked=datetime(2026, 3, 2, 3, tzinfo=plus_five), update=datetime(2026, 1, 1))
        naive = self._row("b", marked=datetime(2026, 3, 1, 23), update=datetime(2026, 2, 1))

        groups = order_index_groups({"naive": [naive], "aware": [aware]})

        assert self._names(groups) == ["aware", "naive"]

    def test_output_holds_only_strings(self):
        groups = order_index_groups({"idx": [self._row("a"), self._row("b")]})

        assert groups == [("idx", ["a", "b"])]
