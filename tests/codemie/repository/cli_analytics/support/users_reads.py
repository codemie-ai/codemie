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

"""Fakes of the reader calls behind `LocalAnalyticsHandler.get_users`, for handler tests.

`install_users_reads` stands in for `get_cost_models`, `get_users_money_page`, `get_avg_session_duration_ms`,
`get_users`, `get_developer_session_facts` and the three per-developer side reads, so a test only supplies the rows it
cares about. The money page ranks like the reader: `round(cost, 9)` descending, then the name in code-point order.
"""

from typing import Any
from unittest.mock import AsyncMock

from codemie.repository.cli_analytics.ports import G_USER, GroupedMoney, UsersMoneyPage


def ranked_page(names: list[str], money: GroupedMoney, limit: int | None, offset: int) -> UsersMoneyPage:
    cost = {g.keys["user"]: g.cost_usd for g in money.groups.get(G_USER, [])}
    ranked = sorted(set(names), key=lambda n: (-round(cost.get(n, 0.0), 9), n))
    page = ranked if limit is None else ranked[offset : offset + limit]
    return UsersMoneyPage(page, len(ranked), money)


def install_users_reads(
    repo: Any,
    user_rows: list[dict],
    *,
    money: GroupedMoney | None = None,
    facts: list[dict] | None = None,
    avg: int | None = None,
    calls: list | None = None,
) -> Any:
    """Wire the Users reads on `repo` (a MagicMock). `user_rows` are `get_users` rows; `money` is the window's money
    (`money` may be a callable of no argument evaluated per call, for a catalog patched by the test)."""
    log = [] if calls is None else calls

    async def models(f):
        log.append("get_cost_models")
        return []

    async def page(f, rates, limit, offset):
        log.append(("get_users_money_page", limit, offset))
        value = money() if callable(money) else (money or GroupedMoney({}))
        return ranked_page([r["developer_name"] for r in user_rows], value, limit, offset)

    async def users(f):
        log.append(("get_users", f.developers))
        return [r for r in user_rows if not f.developers or r["developer_name"] in f.developers]

    async def developer_facts(f):
        log.append(("get_developer_session_facts", f.developers))
        return [r for r in facts or [] if not f.developers or r["developer"] in f.developers]

    async def average(f):
        log.append("get_avg_session_duration_ms")
        return avg

    repo.get_cost_models = models
    repo.get_users_money_page = page
    repo.get_users = users
    repo.get_developer_session_facts = developer_facts
    repo.get_avg_session_duration_ms = average
    for name in ("get_users_daily_activity", "get_users_last_active", "get_lines_by_user"):
        setattr(repo, name, AsyncMock(return_value=[]))
    return repo
