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

"""AC17: the Cost and Efficiency views are unchanged by the Sessions list rework.

`data/cost_efficiency_baseline.json` holds what `get_cost` and `get_efficiency` returned on `main` for the fixed reader
rows below (regenerate it only from `main`: `python <this file>` in a checkout of `main`).
The branch must return the same output.
"""

import asyncio
import json
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codemie.configs.llm_config import CostConfig
from codemie.repository.cli_analytics.filters import LocalAnalyticsFilter
from codemie.repository.cli_analytics.scope import ViewerScope
from codemie.service.analytics.handlers.cli_analytics_handler import LocalAnalyticsHandler

BASELINE = Path(__file__).parent / "data" / "cost_efficiency_baseline.json"
_SCOPE = ViewerScope(viewer_email="admin@x.com", unrestricted=True, admin_projects=frozenset(), mode="exclude")


def _filter() -> LocalAnalyticsFilter:
    return LocalAnalyticsFilter(scope=_SCOPE, start_dt=datetime(2026, 1, 1), end_dt=datetime(2026, 12, 31))


def _model_row(session: str, model: str, normalized: str, **tokens: int | None) -> dict:
    base = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_creation_tokens": 0,
        "cache_creation_5m_tokens": None,
        "cache_creation_1h_tokens": None,
    }
    return {"session_id": session, "model_name": model, "normalized_model_name": normalized, **base, **tokens}


_MODEL_ROWS = [
    _model_row(
        "s1",
        "claude-opus-5-5",
        "claude-opus-5-5",
        input_tokens=374,
        output_tokens=66_483,
        cache_read_tokens=40_356_538,
        cache_creation_tokens=1_888_908,
        cache_creation_1h_tokens=1_888_908,
    ),  # fmt: skip
    _model_row(
        "s2",
        "us.anthropic.claude-sonnet-4-6",
        "claude-sonnet-4-6",
        input_tokens=6,
        output_tokens=708,
        cache_read_tokens=107_215,
        cache_creation_tokens=41_644,
        cache_creation_5m_tokens=0,
        cache_creation_1h_tokens=41_644,
    ),  # fmt: skip
    _model_row("s3", "mystery-model", "mystery-model", input_tokens=900, output_tokens=100),
    _model_row(
        "s3", "claude-opus-5-5", "claude-opus-5-5", input_tokens=10, output_tokens=2_000, cache_read_tokens=5_000
    ),
]
_FACTS = [
    {"session_id": sid, "developer_name": dev, "model_name": "m", "cost_usd": 1.0, "input_tokens": 1, "output_tokens": 1,
     "cache_read_tokens": 0, "cache_creation_tokens": 0, "prompt": f"prompt {sid}",
     "started_at": datetime(2026, 1, day), "last_event_at": datetime(2026, 1, day, 0, 5), "duration_ms": 300_000}
    for sid, dev, day in (("s1", "bob", 1), ("s2", "alice", 2), ("s3", "alice", 3))
]  # fmt: skip
_READS = {
    "get_cost_kpis": [{"total_sessions": 3, "total_cost_usd": 3.0, "total_input_tokens": 1_000,
                       "total_output_tokens": 70_000, "total_cache_read_tokens": 40_468_753,
                       "total_cache_creation_tokens": 1_930_552}],
    "get_cost_by_user": [{"developer_name": "bob", "session_ids": ["s1"]},
                         {"developer_name": "alice", "session_ids": ["s2", "s3"]}],
    "get_session_cost_facts": _FACTS,
    "get_turns_by_session": [{"session_id": "s1", "turns": 4}, {"session_id": "s2", "turns": 2}],
    "get_lines_totals": [{"lines_added": 10, "lines_removed": 2}],
    "get_lines_by_session": [{"session_id": "s1", "lines_added": 10, "lines_removed": 2}],
    "get_file_facts_by_session": [],
}  # fmt: skip


def _handler() -> LocalAnalyticsHandler:
    repo = MagicMock()
    repo.get_class_lists = AsyncMock(return_value=[])
    repo.get_session_model_costs = AsyncMock(return_value=_MODEL_ROWS)
    repo.get_session_funding_costs = AsyncMock(return_value=[])  # the funding buckets of main (EPMCDME-16849)
    for name, rows in _READS.items():
        setattr(repo, name, AsyncMock(return_value=rows))
    return LocalAnalyticsHandler(repo)


def _catalog():
    models = []
    for name, prices in (
        ("claude-opus-5-5", (4.4e-6, 22e-6, 0.22e-6, 5.5e-6, 8.8e-6)),
        ("claude-sonnet-4-6", (3.3e-6, 16.5e-6, 0.33e-6, 4.125e-6, 6.6e-6)),
    ):
        model = MagicMock()
        model.base_name = name
        model.deployment_name = name
        model.cost = CostConfig(
            input=prices[0],
            output=prices[1],
            cache_read_input_token_cost=prices[2],
            cache_creation_input_token_cost=prices[3],
            cache_creation_input_token_cost_above_1hr=prices[4],
        )
        models.append(model)
    module = "codemie.service.analytics.handlers.coding_agent_pricing.llm_service"
    return patch(f"{module}.get_all_llm_model_info", return_value=models), patch(
        f"{module}.catalog_source", return_value="litellm"
    )


async def outputs() -> dict:
    """What the Cost and Efficiency views return for the fixed rows, as JSON-comparable data."""
    models, source = _catalog()
    with models, source:
        handler = _handler()
        cost, cost_pricer = await handler.get_cost(_filter())
        efficiency, efficiency_pricer = await handler.get_efficiency(_filter())
    result = {
        "cost": cost,
        "cost_unpriced": cost_pricer.unpriced,
        "efficiency": efficiency,
        "efficiency_unpriced": efficiency_pricer.unpriced,
    }
    return json.loads(json.dumps(result, default=str, sort_keys=True))


@pytest.mark.asyncio
async def test_cost_and_efficiency_views_equal_the_baseline_captured_on_main():
    assert await outputs() == json.loads(BASELINE.read_text())


@pytest.mark.asyncio
async def test_the_baseline_is_sensitive_to_a_changed_view():
    baseline = json.loads(BASELINE.read_text())
    assert baseline["cost"]["kpis"]["total_cost_usd"] > 0 and baseline["cost_unpriced"] == ["mystery-model"]
    changed = {**baseline, "cost_unpriced": []}
    assert await outputs() != changed


if __name__ == "__main__":  # pragma: no cover - regenerate on main only
    BASELINE.write_text(json.dumps(asyncio.run(outputs()), indent=2, sort_keys=True) + "\n")
