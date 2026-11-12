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

"""/users, /user-charts and /cost reuse a fresh answer for the same viewer and filters (EPMCDME-17051)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import codemie.rest_api.routers.cli_analytics as router_module
from codemie.repository.cli_analytics.scope import ViewerScope
from codemie.rest_api.security.user import User
from tests.codemie.repository.cli_analytics.support.scopes import EXCLUDE_SCOPE

END = datetime(2026, 9, 23, tzinfo=timezone.utc)
OTHER_SCOPE = ViewerScope(
    viewer_email="b@example.com", unrestricted=False, admin_projects=frozenset({"p"}), mode="detail"
)


_COST = {
    "kpis": {"total_sessions": 1, "total_cost_usd": 1.0, "total_tokens": 3, "avg_cost_per_session": 1.0},
    "cost_by_user": [],
    "cost_by_model": [],
    "models_without_spend": [],
}
_USER_ROW = {
    "developer_name": "a@example.com",
    "session_count": 1,
    "input_tokens": 1,
    "output_tokens": 1,
    "cost_usd": 1.0,
    "cache_read_tokens": 0,
    "cache_creation_tokens": 0,
}


def _params(time_period: str = "last_7_days") -> router_module.FilterParams:
    return router_module.FilterParams(
        time_period=time_period,
        start_date=None,
        end_date=None,
        users=None,
        projects=None,
        repositories=None,
        subscription_type=None,
        project_origin=None,
    )


def _handler(pricer: MagicMock) -> MagicMock:
    handler = MagicMock()
    handler.get_cost = AsyncMock(return_value=(_COST, pricer))
    handler.get_users = AsyncMock(
        return_value=({"rows": [dict(_USER_ROW)], "avg_session_duration_ms": None, "total_count": 1}, pricer)
    )
    return handler


async def _call(handler: MagicMock, scope, route: str, **kwargs):
    async def fake_resolve(self, user, project_unattributed=False, personal_only=False):
        return router_module.LocalAnalyticsFilter(scope=scope, start_dt=END - timedelta(days=1), end_dt=END)

    with (
        patch.object(router_module, "_ensure_enabled", return_value=None),
        patch.object(router_module, "_handler", return_value=handler),
        patch.object(router_module, "_attach_user_ids", AsyncMock()),
        patch.object(router_module.FilterParams, "resolve", fake_resolve),
    ):
        response = await getattr(router_module, route)(user=User(id="u1"), filters=_params(), **kwargs)
    return json.loads(response.body)


@pytest.mark.asyncio
async def test_the_same_request_is_answered_from_the_cache():
    handler = _handler(MagicMock(unpriced=["m"], catalog_source="litellm"))

    first = await _call(handler, EXCLUDE_SCOPE, "get_cost")
    second = await _call(handler, EXCLUDE_SCOPE, "get_cost")

    assert handler.get_cost.await_count == 1
    assert first["data"] == second["data"]
    assert second["metadata"]["unpriced_models"] == ["m"] and second["metadata"]["catalog_source"] == "litellm"


@pytest.mark.asyncio
async def test_another_viewer_never_gets_the_answer_cached_for_a_viewer_with_a_wider_scope():
    handler = _handler(MagicMock(unpriced=[], catalog_source="litellm"))

    await _call(handler, EXCLUDE_SCOPE, "get_cost")
    await _call(handler, OTHER_SCOPE, "get_cost")

    assert handler.get_cost.await_count == 2


@pytest.mark.asyncio
async def test_users_pages_and_user_charts_are_separate_answers():
    handler = _handler(MagicMock(unpriced=[], catalog_source="litellm"))

    await _call(
        handler,
        EXCLUDE_SCOPE,
        "get_users",
        page=0,
        per_page=50,
        include_chart=False,
        include_daily_activity=False,
        include_avg_duration=False,
    )
    await _call(
        handler,
        EXCLUDE_SCOPE,
        "get_users",
        page=0,
        per_page=50,
        include_chart=False,
        include_daily_activity=False,
        include_avg_duration=False,
    )
    await _call(
        handler,
        EXCLUDE_SCOPE,
        "get_users",
        page=1,
        per_page=50,
        include_chart=False,
        include_daily_activity=False,
        include_avg_duration=False,
    )
    charts = await _call(handler, EXCLUDE_SCOPE, "get_user_charts")
    await _call(handler, EXCLUDE_SCOPE, "get_user_charts")

    assert handler.get_users.await_count == 3
    # /user-charts asks for every row with every field (its contract), not the money-only read
    assert handler.get_users.await_args_list[-1].kwargs == {}
    assert charts["data"]["total_count"] is None  # set on the caller's copy, not on the cached answer


@pytest.mark.asyncio
async def test_a_users_page_with_the_chart_is_its_own_answer_and_reaches_the_handler():
    handler = _handler(MagicMock(unpriced=[], catalog_source="litellm"))

    await _call(
        handler,
        EXCLUDE_SCOPE,
        "get_users",
        page=0,
        per_page=50,
        include_chart=False,
        include_daily_activity=False,
        include_avg_duration=False,
    )
    await _call(
        handler,
        EXCLUDE_SCOPE,
        "get_users",
        page=0,
        per_page=50,
        include_chart=True,
        include_daily_activity=False,
        include_avg_duration=False,
    )
    await _call(
        handler,
        EXCLUDE_SCOPE,
        "get_users",
        page=0,
        per_page=50,
        include_chart=True,
        include_daily_activity=False,
        include_avg_duration=False,
    )

    assert [c.kwargs for c in handler.get_users.await_args_list] == [
        {"include_chart": False, "include_daily_activity": False, "include_avg_duration": False},
        {"include_chart": True, "include_daily_activity": False, "include_avg_duration": False},
    ]


@pytest.mark.asyncio
async def test_the_optional_reads_are_part_of_the_users_answer_key():
    handler = _handler(MagicMock(unpriced=[], catalog_source="litellm"))
    plain = {"page": 0, "per_page": 50, "include_chart": False}

    await _call(handler, EXCLUDE_SCOPE, "get_users", **plain, include_daily_activity=False, include_avg_duration=False)
    await _call(handler, EXCLUDE_SCOPE, "get_users", **plain, include_daily_activity=True, include_avg_duration=False)
    await _call(handler, EXCLUDE_SCOPE, "get_users", **plain, include_daily_activity=False, include_avg_duration=True)

    assert handler.get_users.await_count == 3


@pytest.mark.asyncio
async def test_a_zero_ttl_turns_the_cache_off():
    handler = _handler(MagicMock(unpriced=[], catalog_source="litellm"))

    with patch.object(
        router_module.config if hasattr(router_module, "config") else MagicMock(),
        "CLI_ANALYTICS_DASHBOARD_CACHE_TTL_S",
        0,
    ):
        await _call(handler, EXCLUDE_SCOPE, "get_cost")
        await _call(handler, EXCLUDE_SCOPE, "get_cost")

    assert handler.get_cost.await_count == 2


def test_the_optional_parts_are_on_unless_a_caller_turns_them_off():
    """A request that sends none of the options gets what it got before they existed: the class lists and buckets of
    /cost, the daily activity and the average duration of /users. Only `chart`, a new field, is opt-in."""
    import inspect

    cost = inspect.signature(router_module.get_cost).parameters
    users = inspect.signature(router_module.get_users).parameters

    assert cost["include_class_lists"].default.default is True
    assert cost["include_buckets"].default.default is True
    assert users["include_daily_activity"].default.default is True
    assert users["include_avg_duration"].default.default is True
    assert users["include_chart"].default.default is False
