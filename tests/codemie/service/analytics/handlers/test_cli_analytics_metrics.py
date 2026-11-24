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

"""A dashboard request writes one log line and its stats metrics, with the endpoint and lane only (EPMCDME-17301)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from prometheus_client import CollectorRegistry, generate_latest

from codemie.configs.prometheus_config import register_cli_analytics_metrics
from codemie.repository.cli_analytics.postgres import request_stats
from codemie.service.analytics.handlers import cli_analytics_handler as handler_module
from codemie.service.analytics.handlers.cli_analytics_handler import dashboard_request


@pytest.fixture
def registry():
    reg = CollectorRegistry()
    register_cli_analytics_metrics(reg)
    yield reg
    request_stats.install_metrics(None)


@pytest.mark.asyncio
async def test_dashboard_request_opens_the_stats_and_writes_one_line(monkeypatch, registry):
    request_stats.current_endpoint.set("get_users")
    log = MagicMock()
    monkeypatch.setattr(handler_module, "logger", log)
    async with dashboard_request():
        stats = request_stats.current()
        assert stats is not None
        assert (stats.endpoint, stats.lane) == ("get_users", "heavy")
        stats.statements = 3
    assert request_stats.current() is None
    lines = [c.args[0] for c in log.info.call_args_list if "cli_analytics request" in c.args[0]]
    assert len(lines) == 1
    assert "endpoint=get_users" in lines[0] and "lane=heavy" in lines[0] and "statements=3" in lines[0]
    assert "@" not in lines[0]
    assert (
        registry.get_sample_value(
            "cli_analytics_request_gate_wait_seconds_count", {"endpoint": "get_users", "lane": "heavy"}
        )
        == 1
    )


@pytest.mark.asyncio
async def test_gate_wait_is_measured_and_labels_hold_only_endpoint_and_lane(registry):
    request_stats.current_endpoint.set("get_cost")
    async with dashboard_request():
        pass
    text = generate_latest(registry).decode()
    request_lines = [ln for ln in text.splitlines() if ln.startswith("cli_analytics_request_") and "_bucket" in ln]
    assert request_lines
    for line in request_lines:
        labels = line[line.index("{") + 1 : line.index("}")]
        assert {kv.split("=")[0] for kv in labels.split(",")} == {"endpoint", "lane", "le"}
