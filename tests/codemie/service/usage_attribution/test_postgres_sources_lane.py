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

"""The Usage Attribution reads of the analytics store run as light-lane dashboard requests, so an engine acquire inside
them is a dashboard's and never waits for the jobs' slot that background jobs hold."""

import asyncio
from datetime import UTC, datetime
from contextlib import AsyncExitStack, asynccontextmanager
from unittest.mock import patch

import pytest

from codemie.repository.cli_analytics.postgres import request_stats
from codemie.service.usage_attribution import postgres_sources


class _Reader:
    """Records whether a dashboard request was open when the read ran, as the engine's acquire sees it."""

    def __init__(self) -> None:
        self.in_request: list[bool] = []

    async def get_skill_names_by_session(self, session_ids, *, scope):
        self.in_request.append(request_stats.current() is not None)
        return [{"session_id": "s1", "skill_names": ["a"]}]

    async def get_framework_sessions(self, start, end, labels):
        self.in_request.append(request_stats.current() is not None)
        return []


@pytest.mark.asyncio
async def test_skill_names_are_read_inside_a_dashboard_request():
    reader = _Reader()

    names = await postgres_sources.skill_names_by_session(reader, ["s1"])  # type: ignore[arg-type]

    assert names == {"s1": ["a"]}
    assert reader.in_request == [True]
    assert request_stats.current() is None


@pytest.mark.asyncio
async def test_framework_sessions_are_read_inside_a_dashboard_request():
    reader = _Reader()
    start, end = datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 2, tzinfo=UTC)

    with (
        patch.object(postgres_sources, "feature_enabled", return_value=True),
        patch("codemie.service.analytics.delivery_framework.get_rule_framework_labels", return_value=["x"]),
        patch.object(postgres_sources, "_storage_reader", return_value=reader),
    ):
        rows = await postgres_sources.pg_framework_rows(start, end)

    assert rows == []
    assert reader.in_request == [True]


@asynccontextmanager
async def _light_lane_held():
    """Every light-lane slot of the pod held, as when the light lane is busy."""
    from codemie.service.analytics.handlers.cli_analytics_handler import LIGHT, _lane_gate, dashboard_slot

    size = _lane_gate(LIGHT)._value
    async with AsyncExitStack() as stack:
        for _ in range(size):
            await stack.enter_async_context(dashboard_slot(LIGHT))
        yield


@pytest.mark.asyncio
async def test_a_busy_light_lane_does_not_stretch_the_skill_names_timeout():
    reader = _Reader()
    async with _light_lane_held():
        with patch.object(postgres_sources, "PG_SKILL_NAMES_TIMEOUT_S", 0.2):
            started = asyncio.get_running_loop().time()
            names = await postgres_sources.skill_names_by_session(reader, ["s1"])  # type: ignore[arg-type]
            waited = asyncio.get_running_loop().time() - started

    assert names == {}  # gave up: the person view falls back to the Elasticsearch keys
    assert waited < 2  # the wait for a slot counted against its own timeout, not the 540 s request budget
    assert reader.in_request == []  # the read never started


@pytest.mark.asyncio
async def test_a_busy_light_lane_does_not_stretch_the_framework_timeout():
    reader = _Reader()
    start, end = datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 2, tzinfo=UTC)
    async with _light_lane_held():
        with (
            patch.object(postgres_sources, "PG_FRAMEWORK_TIMEOUT_S", 0.2),
            patch.object(postgres_sources, "feature_enabled", return_value=True),
            patch("codemie.service.analytics.delivery_framework.get_rule_framework_labels", return_value=["x"]),
            patch.object(postgres_sources, "_storage_reader", return_value=reader),
        ):
            started = asyncio.get_running_loop().time()
            rows = await postgres_sources.pg_framework_rows(start, end)
            waited = asyncio.get_running_loop().time() - started

    assert rows is None  # a failed read: the worker keeps the Elasticsearch counts
    assert waited < 2
    assert reader.in_request == []
