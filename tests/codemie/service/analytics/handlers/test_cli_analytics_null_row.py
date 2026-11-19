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

"""The null-named (unattributed) row rule of the CLI analytics lists: once, last row of the last page."""

import pytest

from codemie.service.analytics.handlers.cli_analytics_handler import _pagination, with_unattributed

NULL_ROW = {"project_name": None}


def _rows(n: int) -> list[dict]:
    return [{"project_name": f"p{i}"} for i in range(n)]


def test_null_row_is_appended_on_the_last_page_only():
    first = with_unattributed(_rows(2), NULL_ROW, _pagination(0, 2, 3))
    last = with_unattributed(_rows(1), NULL_ROW, _pagination(1, 2, 3))
    assert first == _rows(2)
    assert last == [*_rows(1), NULL_ROW]


def test_null_row_goes_outside_per_page_on_a_full_last_page():
    assert with_unattributed(_rows(2), NULL_ROW, _pagination(1, 2, 4)) == [*_rows(2), NULL_ROW]


def test_a_list_with_only_the_null_group_gives_that_row_on_page_zero():
    pagination = _pagination(0, 10, 0)
    assert with_unattributed([], NULL_ROW, pagination) == [NULL_ROW]
    assert pagination["total_count"] == 0 and pagination["has_more"] is False


def test_a_page_beyond_the_last_gives_nothing():
    assert with_unattributed([], NULL_ROW, _pagination(3, 10, 5)) == []


def test_no_null_row_leaves_the_rows_alone():
    assert with_unattributed(_rows(2), None, _pagination(0, 10, 2)) == _rows(2)
    assert with_unattributed([], None, _pagination(0, 10, 0)) == []


@pytest.mark.parametrize("page", [0, 1])
def test_has_more_blocks_the_null_row(page):
    assert with_unattributed(_rows(2), NULL_ROW, _pagination(page, 2, 10)) == _rows(2)
