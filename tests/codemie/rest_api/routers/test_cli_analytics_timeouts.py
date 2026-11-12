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

"""`handle_errors(timeout_504=...)`: a query timeout is a 504 on opted-in endpoints and a 503 elsewhere."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import asyncpg
import pytest

import codemie.rest_api.routers.cli_analytics as router_module
from codemie.core.exceptions import ExtendedHTTPException
from codemie.repository.cli_analytics.ports import RequestBudgetExceeded, StatementTimeout

MESSAGE = "The report took too long to build"


def _endpoint(exc: BaseException, **kwargs):
    @router_module.handle_errors("test endpoint", **kwargs)
    async def endpoint():
        raise exc

    return endpoint


@pytest.mark.asyncio
@pytest.mark.parametrize("exc", [asyncpg.QueryCanceledError("canceled"), StatementTimeout(), RequestBudgetExceeded()])
async def test_timeout_504_maps_query_cancel_and_timeout_error(exc):
    with pytest.raises(ExtendedHTTPException) as opted:
        await _endpoint(exc, timeout_504=MESSAGE)()
    assert opted.value.code == 504
    assert opted.value.message == MESSAGE
    assert "CLI_ANALYTICS_PG_STATEMENT_TIMEOUT_MS" in opted.value.details

    with pytest.raises(ExtendedHTTPException) as plain:
        await _endpoint(exc)()
    assert plain.value.code == 503


@pytest.mark.asyncio
async def test_a_pool_or_connect_timeout_is_a_503_on_an_opted_endpoint_too():
    # a plain TimeoutError never comes from a statement (the reader raises StatementTimeout for those): the store
    # cannot be reached, which must not be reported as a slow query
    with pytest.raises(ExtendedHTTPException) as e:
        await _endpoint(TimeoutError(), timeout_504=MESSAGE)()
    assert e.value.code == 503


@pytest.mark.asyncio
async def test_other_storage_errors_stay_503_on_an_opted_endpoint():
    with pytest.raises(ExtendedHTTPException) as e:
        await _endpoint(asyncpg.InterfaceError("closed"), timeout_504=MESSAGE)()
    assert e.value.code == 503


def test_endpoints_pass_their_timeout_text():
    assert router_module._ERR_SESSIONS_TIMEOUT == "The sessions list took too long to build"
    assert router_module._ERR_DASHBOARD_TIMEOUT


_ROUTES = [
    ("get_cost", {}, "get_cost"),
    ("get_users", {"page": 0, "per_page": 10}, "get_users"),
    ("get_user_charts", {}, "get_users"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("exc", [asyncpg.QueryCanceledError("canceled"), StatementTimeout(), RequestBudgetExceeded()])
@pytest.mark.parametrize(("route", "kwargs", "handler_method"), _ROUTES)
async def test_dashboard_routes_answer_504(route, kwargs, handler_method, exc):
    handler = MagicMock()
    setattr(handler, handler_method, AsyncMock(side_effect=exc))
    filters = MagicMock()
    filters.resolve = AsyncMock(return_value=MagicMock())
    with (
        patch.object(router_module, "_ensure_enabled"),
        patch.object(router_module, "_handler", return_value=handler),
        pytest.raises(ExtendedHTTPException) as e,
    ):
        await getattr(router_module, route)(user=MagicMock(), filters=filters, **kwargs)
    assert e.value.code == 504
    assert e.value.message == router_module._ERR_DASHBOARD_TIMEOUT
