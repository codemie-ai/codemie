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

"""SQL-text seam checks of the viewer scope against the query shapes of MR !4652 (EPMCDME-16440).

!4652 (EPMCDME-16441, squash a80c1922c) is merged and this branch is rebased onto it, so the assertions run on the
SQL of the combined reader. They show that the scope predicate lives in the `_sessions_cte` base conditions and that
the queries !4652 changed subselect `sel` or carry the detail gate in an outer WHERE. The live counterparts
(`test_live_owner_scope.py`, the cost-less, paged, subagent-totals and first-prompt live modules) execute the SQL.

Seam notes, read from the combined reader:
- `_facts_from(include_costless)`: costless branch is `sel s JOIN act a ... LEFT JOIN cost`, costed branch
  `cost JOIN sel s`; both read `sel`.
- `_token_rows` list caller (via `_SEL_SESSIONS`) scopes both branches by `session_id IN (SELECT session_id FROM sel)`;
  `_totals_join` is scoped to `sel` too.
- `get_session_page` `filtered` selects `FROM sel s`.
- The widened `get_session_detail_meta` adds cost-less `session_dims` rows to CTE `c`.
- The detail `_token_rows("c.session_id = $sid::text", "u.session_id = $sid::text")` caller carries `detail_gate`.
"""

import asyncio
from datetime import datetime

from codemie.repository.cli_analytics.filters import LocalAnalyticsFilter
from codemie.repository.cli_analytics.postgres.reader import PostgresCliAnalyticsReader
from codemie.repository.cli_analytics.scope import sql_list_condition
from tests.codemie.repository.cli_analytics.support.scopes import DETAIL_SCOPE, EXCLUDE_SCOPE


_T = datetime(2026, 1, 1)
_F = LocalAnalyticsFilter(start_dt=_T, end_dt=_T, scope=EXCLUDE_SCOPE)
_COND = sql_list_condition(EXCLUDE_SCOPE)[0]


def _capture(call) -> str:
    seen: list[str] = []
    reader = PostgresCliAnalyticsReader(None)  # type: ignore[arg-type]

    async def fake(sql, params):
        seen.append(sql)
        return []

    reader._q = fake  # type: ignore[method-assign]
    asyncio.run(call(reader))
    return seen[0]


def test_scope_predicate_is_the_first_sel_base_condition():
    cte = PostgresCliAnalyticsReader._sessions_cte(_F)
    where = cte.split("WHERE d.started_at IS NOT NULL", 1)[1].split(") x", 1)[1]
    assert where.lstrip().startswith("WHERE " + _COND)
    # The rule does not depend on the !4652 knobs.
    assert not {"include_costless", "restricted_to", "session_ids"} & set(cte.split())


def test_list_queries_read_through_sel_so_the_scope_is_inherited():
    for call in (
        lambda r: r.get_session_cost_facts(_F),
        lambda r: r.get_repository_sessions(_F),
        lambda r: r.get_session_model_costs(_F),
        lambda r: r.get_users(_F),
        lambda r: r.get_cost_by_user(_F),
    ):
        sql = _capture(call)
        assert sql.count(_COND) == 1  # decided once, in sel, not re-derived per query
        assert "FROM sel" in sql or "JOIN sel" in sql or "IN (SELECT session_id FROM sel)" in sql, sql[:200]
        assert sql.index(_COND) < max(sql.rfind("JOIN sel"), sql.rfind("FROM sel"), sql.rfind("FROM sel s"))


def test_detail_queries_gate_in_the_outer_where():
    meta = _capture(lambda r: r.get_session_detail_meta("s", scope=DETAIL_SCOPE))
    outer = meta.rsplit("LEFT JOIN otel_resources", 1)[1]
    assert (
        "WHERE EXISTS (SELECT 1 FROM (SELECT $sid::text AS session_id) g" in outer
    )  # gate filters whatever CTE c yields
    for call in (
        lambda r: r.get_session_detail_cost("s", scope=DETAIL_SCOPE),
        lambda r: r.get_session_detail_model_costs("s", scope=DETAIL_SCOPE),
        lambda r: r.get_session_detail_scalars("s", scope=DETAIL_SCOPE),
        lambda r: r.get_session_detail_tools("s", scope=DETAIL_SCOPE),
        lambda r: r.get_session_detail_events("s", scope=DETAIL_SCOPE),
        lambda r: r.get_session_detail_dispatches("s", scope=DETAIL_SCOPE),
    ):
        assert "LEFT JOIN session_dims d ON d.session_id = g.session_id" in _capture(call)


def test_absent_scope_generates_a_deny_condition():
    f = LocalAnalyticsFilter(start_dt=_T, end_dt=_T, scope=None)
    cte = PostgresCliAnalyticsReader._sessions_cte(f)
    assert cte.split(") x", 1)[1].lstrip().startswith("WHERE false")
