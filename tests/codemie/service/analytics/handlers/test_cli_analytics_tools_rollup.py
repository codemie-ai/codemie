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

"""How the handler takes the Tools & Models rollup (EPMCDME-17301): the rollup's rows build the response with no live
statement, a request that asks for class lists or is refused runs the live statements, and the rollup's answer builds
the same response as the same live rows. The values the rollup holds are checked against the live statements in
`test_live_tools_rollup.py`."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codemie.configs.config import config
from codemie.repository.cli_analytics.filters import LocalAnalyticsFilter
from codemie.repository.cli_analytics.ports import RollupTools
from codemie.service.analytics.dashboard_cache import reset_dashboard_cache
from codemie.service.analytics.handlers.cli_analytics_handler import LocalAnalyticsHandler
from tests.codemie.repository.cli_analytics.support.scopes import EXCLUDE_SCOPE


@pytest.fixture(autouse=True)
def _live_rankings():
    """Overrides the directory's fixture: here the handler's own rollup read runs."""


@pytest.fixture(autouse=True)
def _fresh_cache():
    reset_dashboard_cache()
    yield
    reset_dashboard_cache()


def _f() -> LocalAnalyticsFilter:
    return LocalAnalyticsFilter(scope=EXCLUDE_SCOPE, start_dt=datetime(2026, 9, 1), end_dt=datetime(2026, 9, 8))


_TOOL_ROWS = [{"tool_name": "Read", "call_count": 5, "success_count": 4}]
_MODEL_ROWS = [
    {"model_name": "model-a", "session_count": 2, "total_tokens": 900},
    {"model_name": "model-zero", "session_count": 1, "total_tokens": 0},
    {"model_name": "model-b", "session_count": 1, "total_tokens": 1500},
]
_INVOCATION_ROWS = [
    {"kind": "skill", "name": "brainstorming", "count": 4},
    {"kind": "agent", "name": "Explore", "count": 1},
    {"kind": "command", "name": "/review", "count": 2},
]


def _served() -> RollupTools:
    return RollupTools(list(_TOOL_ROWS), list(_MODEL_ROWS), list(_INVOCATION_ROWS))


def _handler(rollup_answer):
    repo = MagicMock()
    repo.rollup_tools = AsyncMock(return_value=rollup_answer)
    repo.materialize_sel = AsyncMock()
    repo.get_tool_usage = AsyncMock(return_value=_TOOL_ROWS)
    repo.get_model_breakdown = AsyncMock(return_value=_MODEL_ROWS)
    repo.get_invocations = AsyncMock(return_value=_INVOCATION_ROWS)
    repo.get_class_lists = AsyncMock(return_value=[])
    live = MagicMock()
    live.__aenter__ = AsyncMock(return_value=repo)
    live.__aexit__ = AsyncMock(return_value=False)
    repo.bounded = MagicMock(return_value=live)
    return LocalAnalyticsHandler(repo), repo


@pytest.mark.asyncio
async def test_tools_without_class_lists_are_built_from_the_rollup_rows_without_a_live_statement():
    handler, repo = _handler(_served())
    with patch.object(config, "CLI_ANALYTICS_USER_ROLLUP_ENABLED", True):
        data = await handler.get_tools(_f(), include_class_lists=False)

    assert data["tool_usage"] == [
        {
            "tool_name": "Read",
            "call_count": 5,
            "success_count": 4,
            "success_rate": 80.0,
            "subscription_types": [],
            "project_origins": [],
        }
    ]
    assert [m["model_name"] for m in data["tokens_by_model"]] == ["model-b", "model-a"]  # no tokens dropped, by tokens
    assert [s["name"] for s in data["skills_invoked"]] == ["brainstorming"]
    assert [s["name"] for s in data["agent_subtypes"]] == ["Explore"]
    assert [s["name"] for s in data["slash_commands"]] == ["/review"]
    repo.bounded.assert_not_called()
    repo.get_tool_usage.assert_not_called()


@pytest.mark.asyncio
async def test_the_rollup_rows_and_the_same_live_rows_build_the_same_response():
    handler, repo = _handler(_served())
    with patch.object(config, "CLI_ANALYTICS_USER_ROLLUP_ENABLED", True):
        from_rollup = await handler.get_tools(_f(), include_class_lists=False)
    reset_dashboard_cache()  # the first answer must not be served from the cache to the refused read
    handler, repo = _handler(None)
    with patch.object(config, "CLI_ANALYTICS_USER_ROLLUP_ENABLED", True):
        from_live = await handler.get_tools(_f(), include_class_lists=False)

    assert from_rollup == from_live
    repo.get_tool_usage.assert_called_once()  # the refusal ran the live statements


@pytest.mark.asyncio
async def test_tools_with_class_lists_never_ask_the_rollup():
    handler, repo = _handler(_served())
    with patch.object(config, "CLI_ANALYTICS_USER_ROLLUP_ENABLED", True):
        await handler.get_tools(_f(), include_class_lists=True)

    repo.rollup_tools.assert_not_called()
    assert repo.get_class_lists.call_count == 3


@pytest.mark.asyncio
async def test_tools_never_ask_the_rollup_while_it_is_disabled():
    handler, repo = _handler(_served())
    with patch.object(config, "CLI_ANALYTICS_USER_ROLLUP_ENABLED", False):
        await handler.get_tools(_f(), include_class_lists=False)

    repo.rollup_tools.assert_not_called()
    repo.get_tool_usage.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("order", ["b-first", "a-first"])
async def test_models_with_equal_tokens_are_ordered_by_name_whatever_the_order_of_the_rows(order):
    models = [
        {"model_name": "model-b", "session_count": 1, "total_tokens": 500},
        {"model_name": "model-a", "session_count": 9, "total_tokens": 500},
        {"model_name": "model-c", "session_count": 1, "total_tokens": 900},
    ]
    served = RollupTools(list(_TOOL_ROWS), models if order == "b-first" else list(reversed(models)), [])
    handler, _ = _handler(served)
    with patch.object(config, "CLI_ANALYTICS_USER_ROLLUP_ENABLED", True):
        data = await handler.get_tools(_f(), include_class_lists=False)

    assert [m["model_name"] for m in data["tokens_by_model"]] == ["model-c", "model-a", "model-b"]
