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

"""Live-Postgres checks for EPMCDME-17051: the `developer` column of `sel` and the `developers` filter.

Skipped when the local analytics Postgres named by `TEST_LOCAL_PG_URL` is unreachable.
"""

from datetime import timedelta

import pytest

from codemie.repository.cli_analytics.filters import LocalAnalyticsFilter
from codemie.repository.cli_analytics.postgres.reader import PostgresCliAnalyticsReader
from tests.codemie.repository.cli_analytics.postgres.test_live_session_subagent_cost import (
    _NOW,
    _START,
    _cleanup,
    _get_engine,
    _refresh,
)
from tests.codemie.repository.cli_analytics.postgres.live_helpers import _seed_requests, _seed_start
from tests.codemie.repository.cli_analytics.support.scopes import EXCLUDE_SCOPE

_SID = "test-epmcdme-17051-developer-filter"
_FALLBACK_EMAIL = "fallback.dev@x.com"


def _filter(**kwargs) -> LocalAnalyticsFilter:
    return LocalAnalyticsFilter(
        scope=EXCLUDE_SCOPE, start_dt=_START - timedelta(days=1), end_dt=_NOW + timedelta(days=1), **kwargs
    )


async def _ids(reader: PostgresCliAnalyticsReader, f: LocalAnalyticsFilter, with_developer: bool) -> list[dict]:
    cte = reader._sessions_cte(f, with_developer=with_developer)
    columns = "session_id, developer" if with_developer else "session_id"
    rows = await reader._q(
        f"WITH {cte} SELECT {columns} FROM sel WHERE session_id = $sid::text", {**f.params(), "sid": _SID}
    )
    return rows


@pytest.mark.asyncio
async def test_developers_filter_keeps_sessions_named_by_fallback_email():
    engine = await _get_engine()
    try:
        await _cleanup(engine, _SID)
        await _seed_start(engine, _SID)
        await _seed_requests(engine, _SID)
        await _refresh(engine)
        async with engine.acquire() as conn:
            await conn.execute("UPDATE cost_daily SET user_email = $2 WHERE session_id = $1", _SID, _FALLBACK_EMAIL)
            await conn.execute(
                "UPDATE session_dims SET jwt_email = NULL, dev_name_max = NULL, developer_name = NULL"
                " WHERE session_id = $1",
                _SID,
            )
        reader = PostgresCliAnalyticsReader(engine)

        named = await _ids(reader, _filter(developers=[_FALLBACK_EMAIL]), with_developer=True)
        assert named == [{"session_id": _SID, "developer": _FALLBACK_EMAIL}]
        assert await _ids(reader, _filter(users=[_FALLBACK_EMAIL]), with_developer=False) == []
        assert await _ids(reader, _filter(developers=["someone.else@x.com"]), with_developer=True) == []
    finally:
        await _cleanup(engine, _SID)
        await engine.close()


def test_developers_filter_without_the_developer_column_is_rejected():
    with pytest.raises(ValueError, match="developers"):
        PostgresCliAnalyticsReader._sessions_cte(_filter(developers=["a@x.com"]))


def test_sel_without_developer_is_unchanged():
    f = _filter(users=["a@x.com"])
    default = PostgresCliAnalyticsReader._sessions_cte(f)
    assert default == PostgresCliAnalyticsReader._sessions_cte(f, with_developer=False)
    assert "developer" not in default.replace("developer_name", "").replace("dev_name_max", "")
    assert default.lstrip().startswith("sel AS (SELECT * FROM (")
