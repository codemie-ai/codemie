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

"""With `unattributed_last` the repositories list pages named repositories only and the one without a repository is the
last row of the last page; by default that group is ranked and counted like any row."""

import pytest

from tests.codemie.service.analytics.handlers.test_cli_analytics_handler_ordering import _cost_row, _flt, _handler

ROWS = [
    *(_cost_row(f"a{i}", repository="repo-a") for i in range(3)),
    *(_cost_row(f"b{i}", repository="repo-b") for i in range(2)),
    _cost_row("c0", repository="repo-c"),
    _cost_row("n0", repository=""),
    _cost_row("n1", repository=""),
]


def _names(data: dict) -> list:
    return [r["repository"] for r in data["rows"]]


@pytest.mark.asyncio
async def test_null_repository_row_is_last_on_the_last_page_only():
    first, _ = await _handler(cost_facts=ROWS).get_repositories(_flt(), 0, 2, False, unattributed_last=True)
    last, _ = await _handler(cost_facts=ROWS).get_repositories(_flt(), 1, 2, False, unattributed_last=True)

    assert _names(first) == ["repo-a", "repo-b"]
    assert first["pagination"] == {"page": 0, "per_page": 2, "total_count": 3, "has_more": True}
    assert _names(last) == ["repo-c", None]
    assert last["pagination"] == {"page": 1, "per_page": 2, "total_count": 3, "has_more": False}
    assert last["rows"][-1]["session_count"] == 2


@pytest.mark.asyncio
async def test_null_repository_row_rides_outside_a_full_last_page():
    data, _ = await _handler(cost_facts=ROWS).get_repositories(_flt(), 0, 3, False, unattributed_last=True)

    assert _names(data) == ["repo-a", "repo-b", "repo-c", None]
    assert data["pagination"]["total_count"] == 3


@pytest.mark.asyncio
async def test_null_repository_row_is_absent_under_search():
    data, _ = await _handler(cost_facts=ROWS).get_repositories(_flt(), 0, 10, False, "repo", unattributed_last=True)

    assert None not in _names(data)


@pytest.mark.asyncio
async def test_only_unattributed_sessions_give_that_row_on_page_zero():
    data, _ = await _handler(cost_facts=[_cost_row("n0", repository="")]).get_repositories(
        _flt(), 0, 10, False, unattributed_last=True
    )

    assert _names(data) == [None]
    assert data["pagination"] == {"page": 0, "per_page": 10, "total_count": 0, "has_more": False}


@pytest.mark.asyncio
async def test_no_sessions_give_no_rows():
    data, _ = await _handler(cost_facts=[]).get_repositories(_flt(), 0, 10, False, unattributed_last=True)

    assert data["rows"] == []
    assert data["pagination"]["total_count"] == 0


@pytest.mark.asyncio
async def test_unpaged_all_mode_keeps_every_group_with_branches():
    data, _ = await _handler(cost_facts=ROWS).get_repositories(_flt(), 0, 100, True)

    assert sorted(_names(data), key=lambda n: n or "") == [None, "repo-a", "repo-b", "repo-c"]
    assert data["pagination"]["has_more"] is False


@pytest.mark.asyncio
async def test_by_default_the_null_repository_group_is_ranked_and_counted_like_any_row():
    # 3 named repositories + the group without one = 4 groups, ranked by session count (3, 2, 2, 1) and paged together
    first, _ = await _handler(cost_facts=ROWS).get_repositories(_flt(), 0, 2, False)
    last, _ = await _handler(cost_facts=ROWS).get_repositories(_flt(), 1, 2, False)

    assert first["pagination"] == {"page": 0, "per_page": 2, "total_count": 4, "has_more": True}
    assert last["pagination"] == {"page": 1, "per_page": 2, "total_count": 4, "has_more": False}
    assert len(first["rows"]) == len(last["rows"]) == 2  # nothing rides outside per_page
    assert sorted(n or "" for n in _names(first) + _names(last)) == ["", "repo-a", "repo-b", "repo-c"]
    assert _names(last)[-1] == "repo-c"
