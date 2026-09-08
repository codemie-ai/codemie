# Coding Agents Read Endpoints Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement 6 read-only GET endpoints under `/v1/analytics/coding-agents/` that serve Claude Code session analytics from ClickHouse.

**Architecture:** New standalone vertical — ClickHouse client singleton → repository (parameterized SQL) → handler (assembly + pricing) → FastAPI router — wired into `main.py` with one `include_router` call. No changes to existing analytics, ES, or PostgreSQL code paths.

**Tech Stack:** FastAPI, Pydantic v2, `clickhouse-connect` (HTTP driver), `asyncio.to_thread` wrapper, pytest + AsyncMock.

## Global Constraints

- `from __future__ import annotations` at the top of every new Python file.
- Apache 2.0 copyright header in every new source file (see any existing file for exact text).
- All SQL uses `{name:Type}` ClickHouse parameterized syntax — no f-string SQL interpolation anywhere.
- `clickhouse-connect` uses HTTP port 8123, database `codemie_analytics`.
- All string filter params clamped to 256 chars before reaching SQL.
- Auth: router-level `dependencies=[Depends(authenticate)]` — identical to `analytics.py`.
- `handle_analytics_errors` and `_create_response` imported from `codemie.rest_api.routers.analytics`.
- v1 stub fields: `active_ms=None`, all line/file counts `=0`, `languages=[]`, `agent/skill/command_invocations=[]`, `dispatches=[]`.
- `SummingMergeTree` table `coding_agent_cost_daily` — always use `sum()` aggregates, never raw column values.

---

## File Map

| Action | Path |
|---|---|
| Create | `src/codemie/clients/clickhouse.py` |
| Modify | `src/codemie/configs/config.py` |
| Create | `src/codemie/rest_api/models/coding_agents_analytics.py` |
| Create | `src/codemie/service/analytics/handlers/coding_agent_pricing.py` |
| Create | `src/codemie/repository/coding_agent_analytics_repository.py` |
| Create | `src/codemie/service/analytics/handlers/coding_agents_handler.py` |
| Create | `src/codemie/rest_api/routers/coding_agents_analytics.py` |
| Modify | `src/codemie/rest_api/main.py` |
| Modify | `pyproject.toml` |
| Create | `tests/codemie/clients/test_clickhouse.py` |
| Create | `tests/codemie/rest_api/models/test_coding_agents_analytics.py` |
| Create | `tests/codemie/service/analytics/handlers/test_coding_agent_pricing.py` |
| Create | `tests/codemie/repository/test_coding_agent_analytics_repository.py` |
| Create | `tests/codemie/service/analytics/handlers/test_coding_agents_handler.py` |
| Create | `tests/codemie/rest_api/routers/test_coding_agents_analytics.py` |

---

### Task 1: Foundation — Dependency, Config, ClickHouse Client

**Files:**
- Modify: `pyproject.toml` (under `[tool.poetry.dependencies]`)
- Modify: `src/codemie/configs/config.py` (add 5 fields after existing POSTGRES_* block)
- Create: `src/codemie/clients/clickhouse.py`
- Create: `tests/codemie/clients/test_clickhouse.py`

**Interfaces:**
- Produces: `ch_query(sql: str, params: dict) -> list[dict]` async function, importable from `codemie.clients.clickhouse`
- Produces: `get_client() -> clickhouse_connect.driver.Client` function used by `ch_query`

- [ ] **Step 1: Write the failing tests**

```python
# tests/codemie/clients/test_clickhouse.py
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
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import codemie.clients.clickhouse as ch_module


@pytest.fixture(autouse=True)
def reset_singleton():
    """Clear the module-level singleton before and after each test."""
    ch_module._client = None
    yield
    ch_module._client = None


def test_get_client_calls_connect_only_once():
    mock_client = MagicMock()
    with patch("codemie.clients.clickhouse.clickhouse_connect.get_client", return_value=mock_client) as mock_factory:
        c1 = ch_module.get_client()
        c2 = ch_module.get_client()
    mock_factory.assert_called_once()
    assert c1 is c2


def test_get_client_passes_config_values():
    mock_client = MagicMock()
    with (
        patch("codemie.clients.clickhouse.clickhouse_connect.get_client", return_value=mock_client) as mock_factory,
        patch("codemie.clients.clickhouse.config") as mock_cfg,
    ):
        mock_cfg.CLICKHOUSE_HOST = "ch-host"
        mock_cfg.CLICKHOUSE_PORT = 9000
        mock_cfg.CLICKHOUSE_USER = "admin"
        mock_cfg.CLICKHOUSE_PASSWORD = "secret"
        mock_cfg.CLICKHOUSE_QUERY_TIMEOUT_SECONDS = 60
        ch_module.get_client()
    mock_factory.assert_called_once_with(
        host="ch-host",
        port=9000,
        username="admin",
        password="secret",
        database="codemie_analytics",
        settings={"max_execution_time": 60},
    )


@pytest.mark.asyncio
async def test_ch_query_returns_named_results():
    mock_result = MagicMock()
    mock_result.named_results.return_value = [{"session_id": "abc", "cost_usd": 1.5}]
    mock_client = MagicMock()
    mock_client.query.return_value = mock_result

    async def fake_to_thread(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    with (
        patch("codemie.clients.clickhouse.get_client", return_value=mock_client),
        patch("codemie.clients.clickhouse.asyncio.to_thread", side_effect=fake_to_thread),
    ):
        rows = await ch_module.ch_query("SELECT 1", {"k": "v"})

    mock_client.query.assert_called_once_with("SELECT 1", parameters={"k": "v"})
    assert rows == [{"session_id": "abc", "cost_usd": 1.5}]
```

- [ ] **Step 2: Run tests — expect ImportError (module doesn't exist yet)**

```bash
poetry run pytest tests/codemie/clients/test_clickhouse.py -v
```

Expected: `ImportError: No module named 'codemie.clients.clickhouse'`

- [ ] **Step 3: Add `clickhouse-connect` to `pyproject.toml`**

In `pyproject.toml`, under `[tool.poetry.dependencies]`, add after `asyncpg`:
```toml
clickhouse-connect = "^0.8"
```

Then run:
```bash
poetry lock --no-update && poetry install
```

Expected: resolves without error; `poetry show clickhouse-connect` prints the installed version.

- [ ] **Step 4: Add ClickHouse config fields to `config.py`**

In `src/codemie/configs/config.py`, after the `POSTGRES_PASSWORD` block (around line 70), add:

```python
    CLICKHOUSE_HOST: str = "localhost"
    CLICKHOUSE_PORT: int = 8123
    CLICKHOUSE_USER: str = "default"
    CLICKHOUSE_PASSWORD: str = ""
    CLICKHOUSE_QUERY_TIMEOUT_SECONDS: int = 30
```

- [ ] **Step 5: Create `src/codemie/clients/clickhouse.py`**

```python
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
from __future__ import annotations

import asyncio

import clickhouse_connect

from codemie.configs.config import config

_client: clickhouse_connect.driver.Client | None = None


def get_client() -> clickhouse_connect.driver.Client:
    global _client
    if _client is None:
        _client = clickhouse_connect.get_client(
            host=config.CLICKHOUSE_HOST,
            port=config.CLICKHOUSE_PORT,
            username=config.CLICKHOUSE_USER,
            password=config.CLICKHOUSE_PASSWORD,
            database="codemie_analytics",
            settings={"max_execution_time": config.CLICKHOUSE_QUERY_TIMEOUT_SECONDS},
        )
    return _client


async def ch_query(sql: str, params: dict) -> list[dict]:
    result = await asyncio.to_thread(get_client().query, sql, parameters=params)
    return result.named_results()
```

- [ ] **Step 6: Run tests — expect PASS**

```bash
poetry run pytest tests/codemie/clients/test_clickhouse.py -v
```

Expected: `3 passed`

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml poetry.lock src/codemie/configs/config.py src/codemie/clients/clickhouse.py tests/codemie/clients/test_clickhouse.py
git commit -m "feat(EPMCDME-13561): add clickhouse-connect dependency, config fields, and async client singleton"
```

---

### Task 2: Pydantic Response Models

**Files:**
- Create: `src/codemie/rest_api/models/coding_agents_analytics.py`
- Create: `tests/codemie/rest_api/models/test_coding_agents_analytics.py`

**Interfaces:**
- Consumes: nothing from prior tasks
- Produces: all response model classes used by handler and router:
  `TokenUsage`, `ToolStats`, `NamedInvocationStats`, `ModelCost`, `CostSeriesPoint`,
  `DispatchEvent`, `SessionSummary`, `SessionDetail`, `AggregateToolRow`, `ReportTotals`,
  `SessionsMeta`, `SessionsResponse`, `AnalyticsRootResponse`, `CostRow`, `CostResponse`,
  `ToolsResponse`, `UserRow`, `UsersResponse`

- [ ] **Step 1: Write the failing tests**

```python
# tests/codemie/rest_api/models/test_coding_agents_analytics.py
# Copyright 2026 EPAM Systems, Inc. ("EPAM")
#
# Licensed under the Apache License, Version 2.0 (the "License")
from __future__ import annotations

import pytest

from codemie.rest_api.models.coding_agents_analytics import (
    AggregateToolRow,
    AnalyticsRootResponse,
    CostResponse,
    CostRow,
    ModelCost,
    ReportTotals,
    SessionDetail,
    SessionsMeta,
    SessionsResponse,
    SessionSummary,
    TokenUsage,
    ToolStats,
    ToolsResponse,
    UserRow,
    UsersResponse,
)


def test_token_usage_stores_caller_provided_total():
    t = TokenUsage(input=100, output=50, cache_read=10, cache_creation=5, total=165)
    assert t.total == 165


def test_session_detail_is_subclass_of_session_summary():
    assert issubclass(SessionDetail, SessionSummary)


def test_sessions_response_structure():
    totals = ReportTotals(
        sessions=1, duration_ms=1000, turns=5, files=0, net_lines=0,
        tool_calls_total=10, tool_success_rate=90.0, total_cost_usd=1.5,
        cache_read_cost_usd=0.1, priced_sessions=1,
    )
    meta = SessionsMeta(
        generated_at="2026-07-20T00:00:00Z",
        range_label="2026-06-20..2026-07-20",
        scope="all", user=None, agents=["claude-code"], projects=[],
        totals=totals, unpriced_models=[],
    )
    resp = SessionsResponse(meta=meta, sessions=[], total_count=1)
    assert resp.total_count == 1
    assert resp.meta.scope == "all"


def test_cost_row_serialises():
    row = CostRow(
        day="2026-07-20", user_email="dev@x.com", team_name="backend",
        model_name="claude-sonnet-4-6", query_source="repl",
        cost_usd=1.5, input_tokens=1000, output_tokens=500,
        cache_read_tokens=200, cache_creation_tokens=100, api_call_count=3,
    )
    assert row.day == "2026-07-20"


def test_cost_response_has_totals():
    row = CostRow(
        day="2026-07-20", user_email="dev@x.com", team_name="backend",
        model_name="claude-sonnet-4-6", query_source="repl",
        cost_usd=1.5, input_tokens=1000, output_tokens=500,
        cache_read_tokens=200, cache_creation_tokens=100, api_call_count=3,
    )
    resp = CostResponse(data=[row], total_cost_usd=1.5, total_api_calls=3)
    assert len(resp.data) == 1
    assert resp.total_cost_usd == 1.5


def test_tools_response():
    row = AggregateToolRow(
        tool_name="Bash", developer_name="dev@x.com", team_name="backend",
        total_calls=10, success_count=9, failure_count=1, success_rate=90.0,
    )
    resp = ToolsResponse(tools=[row], total_tool_calls=10)
    assert resp.total_tool_calls == 10


def test_users_response():
    row = UserRow(
        user_email="dev@x.com", team_name="backend",
        total_cost_usd=50.0, total_api_calls=100,
        total_input_tokens=1000, total_output_tokens=500, total_tokens=1500,
        total_sessions=5, turns=0, tool_calls_total=0, net_lines=0,
        last_active_ms=1753001600000,
    )
    resp = UsersResponse(users=[row])
    assert len(resp.users) == 1


def test_analytics_root_response():
    resp = AnalyticsRootResponse(
        distinct_users=3, distinct_teams=2, total_cost_usd=100.0,
        date_range_min="2026-06-01", date_range_max="2026-07-20",
        users=["a@x.com"], teams=["backend"], models=["claude-sonnet-4-6"],
        agents=["claude-code"], projects=[],
    )
    assert resp.distinct_users == 3
    assert resp.projects == []


def test_model_cost_unpriced_flag():
    tokens = TokenUsage(input=0, output=0, cache_read=0, cache_creation=0, total=0)
    mc = ModelCost(model_name="unknown-model", tokens=tokens, cost_usd=0.0, unpriced=True)
    assert mc.unpriced is True
```

- [ ] **Step 2: Run tests — expect ImportError**

```bash
poetry run pytest tests/codemie/rest_api/models/test_coding_agents_analytics.py -v
```

Expected: `ImportError: cannot import name 'TokenUsage' from 'codemie.rest_api.models.coding_agents_analytics'`

- [ ] **Step 3: Create `src/codemie/rest_api/models/coding_agents_analytics.py`**

```python
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
from __future__ import annotations

from pydantic import BaseModel


class TokenUsage(BaseModel):
    input: int
    output: int
    cache_read: int
    cache_creation: int
    total: int  # = input + output + cache_read + cache_creation; caller computes


class ToolStats(BaseModel):
    tool_name: str
    total_calls: int
    success_count: int
    failure_count: int
    success_rate: float  # [0, 100], one decimal


class NamedInvocationStats(BaseModel):
    name: str
    total_calls: int
    success_count: int
    failure_count: int


class ModelCost(BaseModel):
    model_name: str
    tokens: TokenUsage
    cost_usd: float
    unpriced: bool  # True when cost_usd == 0


class CostSeriesPoint(BaseModel):
    t: int    # event.sequence ordinal (1-based)
    cost: float   # cumulative cost USD
    tokens: int   # cumulative total tokens


class DispatchEvent(BaseModel):
    kind: str   # 'agent' | 'skill' | 'command' | 'tool'
    name: str
    start: int  # epoch ms
    duration_ms: int


class SessionSummary(BaseModel):
    # Identity
    session_id: str
    user: str
    developer_name: str
    agent_name: str
    provider: str
    team_name: str

    # Location
    project: str
    branch: str
    cwd: str
    permission_mode: str

    # Session identity
    title: str
    first_prompt: str

    # Timing (epoch ms)
    start_time: int
    duration_ms: int
    active_ms: int | None  # null in v1

    # Activity
    turns: int
    file_ops: int
    lines_added: int
    lines_removed: int
    lines_modified: int
    net_lines: int
    files_changed: int
    files_written: int
    files_edited: int

    # Tools
    tool_calls_total: int
    tool_calls_success: int
    tool_calls_failure: int
    tools: list[ToolStats]

    # Models and tokens
    models: list[str]
    languages: list[str]  # [] in v1
    tokens: TokenUsage

    # Cost
    cost_usd: float
    cache_read_cost_usd: float
    per_model_cost: list[ModelCost]
    priced: bool

    # Dispatch rollups — all [] in v1
    agent_invocations: list[NamedInvocationStats]
    skill_invocations: list[NamedInvocationStats]
    command_invocations: list[NamedInvocationStats]


class SessionDetail(SessionSummary):
    cost_series: list[CostSeriesPoint]
    dispatches: list[DispatchEvent]  # [] in v1
    tool_events: list[dict]


class AggregateToolRow(BaseModel):
    tool_name: str
    developer_name: str
    team_name: str
    total_calls: int
    success_count: int
    failure_count: int
    success_rate: float  # [0, 100], one decimal


class ReportTotals(BaseModel):
    sessions: int
    duration_ms: int
    turns: int
    files: int       # 0 in v1
    net_lines: int   # 0 in v1
    tool_calls_total: int
    tool_success_rate: float  # [0, 100], one decimal
    total_cost_usd: float
    cache_read_cost_usd: float
    priced_sessions: int


class SessionsMeta(BaseModel):
    generated_at: str   # ISO datetime
    range_label: str    # f"{from_date}..{to_date}"
    scope: str          # 'all' | 'user'
    user: str | None
    agents: list[str]
    projects: list[str]
    totals: ReportTotals
    unpriced_models: list[str]


class SessionsResponse(BaseModel):
    meta: SessionsMeta
    sessions: list[SessionSummary]
    total_count: int


class AnalyticsRootResponse(BaseModel):
    distinct_users: int
    distinct_teams: int
    total_cost_usd: float
    date_range_min: str | None
    date_range_max: str | None
    users: list[str]
    teams: list[str]
    models: list[str]
    agents: list[str]
    projects: list[str]


class CostRow(BaseModel):
    day: str
    user_email: str
    team_name: str
    model_name: str
    query_source: str
    cost_usd: float
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_creation_tokens: int
    api_call_count: int


class CostResponse(BaseModel):
    data: list[CostRow]
    total_cost_usd: float
    total_api_calls: int


class ToolsResponse(BaseModel):
    tools: list[AggregateToolRow]
    total_tool_calls: int


class UserRow(BaseModel):
    user_email: str
    team_name: str
    total_cost_usd: float
    total_api_calls: int
    total_input_tokens: int
    total_output_tokens: int
    total_tokens: int
    total_sessions: int
    turns: int        # 0 in v1 — identity gap
    tool_calls_total: int  # 0 in v1 — identity gap
    net_lines: int    # 0 in v1
    last_active_ms: int | None


class UsersResponse(BaseModel):
    users: list[UserRow]
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
poetry run pytest tests/codemie/rest_api/models/test_coding_agents_analytics.py -v
```

Expected: `9 passed`

- [ ] **Step 5: Commit**

```bash
git add src/codemie/rest_api/models/coding_agents_analytics.py tests/codemie/rest_api/models/test_coding_agents_analytics.py
git commit -m "feat(EPMCDME-13561): add Pydantic response models for coding-agents analytics"
```

---

### Task 3: Pricing Module — REMOVED

> **Status: Removed.** All cost metrics — including `cost_usd` and `cache_read_cost_usd` —
> are sourced directly from Claude Code via the `claude_code.cost.usage` OTel metric.
> `coding_agent_pricing.py` and its tests are no longer part of the implementation.
> Skip to Task 4.

~~**Files:**
- Create: `src/codemie/service/analytics/handlers/coding_agent_pricing.py`
- Create: `tests/codemie/service/analytics/handlers/test_coding_agent_pricing.py`~~

- [ ] **Step 1: Write the failing tests**

```python
# tests/codemie/service/analytics/handlers/test_coding_agent_pricing.py
# Copyright 2026 EPAM Systems, Inc. ("EPAM")
#
# Licensed under the Apache License, Version 2.0 (the "License")
from __future__ import annotations

import pytest

from codemie.service.analytics.handlers.coding_agent_pricing import (
    cache_read_cost,
    lookup_price,
    normalize_model,
)


class TestNormalizeModel:
    def test_passthrough_bare_id(self):
        assert normalize_model("claude-sonnet-4-6") == "claude-sonnet-4-6"

    def test_strips_anthropic_dot_prefix(self):
        assert normalize_model("anthropic.claude-sonnet-4-6") == "claude-sonnet-4-6"

    def test_strips_us_anthropic_prefix(self):
        assert normalize_model("us.anthropic.claude-opus-4-8") == "claude-opus-4-8"

    def test_strips_eu_anthropic_prefix(self):
        assert normalize_model("eu.anthropic.claude-haiku-4") == "claude-haiku-4"


class TestLookupPrice:
    def test_exact_match_returns_rates(self):
        result = lookup_price("claude-sonnet-4")
        assert result is not None
        assert result["cache_read"] == 0.3

    def test_prefix_match_returns_family_rates(self):
        # claude-sonnet-4-6 → prefix matches "claude-sonnet-4"
        result = lookup_price("claude-sonnet-4-6")
        assert result is not None
        assert result["cache_read"] == 0.3

    def test_bedrock_id_normalised_then_matched(self):
        result = lookup_price("anthropic.claude-sonnet-4-6")
        assert result is not None
        assert result["input"] == 3.0

    def test_unknown_model_returns_none(self):
        assert lookup_price("gpt-4-turbo") is None

    def test_longest_prefix_wins(self):
        # "claude-3-5-sonnet" is more specific than "claude-3-5"
        result_specific = lookup_price("claude-3-5-sonnet-20241022")
        assert result_specific is not None
        assert result_specific["input"] == 3.0  # claude-3-5-sonnet rate


class TestCacheReadCost:
    def test_known_model_computes_usd(self):
        # claude-sonnet-4: cache_read = 0.3 USD/1M tokens
        cost = cache_read_cost("claude-sonnet-4", 1_000_000)
        assert abs(cost - 0.3) < 1e-9

    def test_unknown_model_returns_zero(self):
        assert cache_read_cost("gpt-4-unknown", 1_000_000) == 0.0

    def test_zero_tokens_returns_zero(self):
        assert cache_read_cost("claude-sonnet-4", 0) == 0.0

    def test_fractional_tokens(self):
        # 500_000 tokens at 0.3/M = 0.15
        cost = cache_read_cost("claude-sonnet-4", 500_000)
        assert abs(cost - 0.15) < 1e-9
```

- [ ] **Step 2: Run tests — expect ImportError**

```bash
poetry run pytest tests/codemie/service/analytics/handlers/test_coding_agent_pricing.py -v
```

Expected: `ImportError: cannot import name 'normalize_model' from 'codemie.service.analytics.handlers.coding_agent_pricing'`

- [ ] **Step 3: Create `src/codemie/service/analytics/handlers/coding_agent_pricing.py`**

```python
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
from __future__ import annotations

# Rates: USD per 1,000,000 tokens
PRICING_TABLE: dict[str, dict] = {
    "claude-opus-4":     {"input": 5.0,  "output": 25.0, "cache_read": 0.5,  "cache_write": 6.25},
    "claude-sonnet-4":   {"input": 3.0,  "output": 15.0, "cache_read": 0.3,  "cache_write": 3.75},
    "claude-haiku-4":    {"input": 1.0,  "output": 5.0,  "cache_read": 0.1,  "cache_write": 1.25},
    "claude-3-opus":     {"input": 15.0, "output": 75.0, "cache_read": 1.5,  "cache_write": 18.75},
    "claude-3-7-sonnet": {"input": 3.0,  "output": 15.0, "cache_read": 0.3,  "cache_write": 3.75},
    "claude-3-5-sonnet": {"input": 3.0,  "output": 15.0, "cache_read": 0.3,  "cache_write": 3.75},
    "claude-3-5-haiku":  {"input": 0.8,  "output": 4.0,  "cache_read": 0.08, "cache_write": 1.0},
    "claude-3-haiku":    {"input": 0.25, "output": 1.25, "cache_read": 0.03, "cache_write": 0.3},
    "claude-3-sonnet":   {"input": 3.0,  "output": 15.0, "cache_read": 0.3,  "cache_write": 3.75},
}


def normalize_model(model: str) -> str:
    """Strip AWS Bedrock prefix (e.g. 'anthropic.' or 'us.anthropic.'); return bare claude-* id."""
    idx = model.rfind("anthropic.")
    if idx != -1:
        return model[idx + len("anthropic."):]
    return model


def lookup_price(model: str) -> dict | None:
    """Exact match, then longest family-prefix match. Returns None if unpriced."""
    normalized = normalize_model(model)
    if normalized in PRICING_TABLE:
        return PRICING_TABLE[normalized]
    best_key: str | None = None
    for key in PRICING_TABLE:
        if normalized.startswith(key):
            if best_key is None or len(key) > len(best_key):
                best_key = key
    return PRICING_TABLE[best_key] if best_key else None


def cache_read_cost(model: str, cache_read_tokens: int) -> float:
    """Return USD cost for cache_read_tokens at this model's cache_read rate."""
    if cache_read_tokens == 0:
        return 0.0
    pricing = lookup_price(model)
    if pricing is None:
        return 0.0
    return pricing["cache_read"] * cache_read_tokens / 1_000_000
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
poetry run pytest tests/codemie/service/analytics/handlers/test_coding_agent_pricing.py -v
```

Expected: `11 passed`

- [ ] **Step 5: Commit**

```bash
git add src/codemie/service/analytics/handlers/coding_agent_pricing.py tests/codemie/service/analytics/handlers/test_coding_agent_pricing.py
git commit -m "feat(EPMCDME-13561): add model pricing table with normalize/lookup/cost helpers"
```

---

### Task 4: Repository Layer

**Files:**
- Create: `src/codemie/repository/coding_agent_analytics_repository.py`
- Create: `tests/codemie/repository/test_coding_agent_analytics_repository.py`

**Interfaces:**
- Consumes: `ch_query` from `codemie.clients.clickhouse` (injected via constructor for testability)
- Produces: `CodingAgentAnalyticsRepository` class with async methods:
  - `get_root_scalars() -> list[dict]`
  - `get_root_lists() -> list[dict]`
  - `get_cost(from_date, to_date, user, team, model) -> list[dict]`
  - `get_sessions(from_date, to_date, user, team, limit, offset) -> list[dict]`
  - `count_sessions(from_date, to_date, user, team) -> int`
  - `get_sessions_per_model_cost(session_ids: list[str]) -> list[dict]`
  - `get_sessions_tool_stats(session_ids: list[str]) -> list[dict]`
  - `get_session_detail(session_id: str) -> list[dict]`
  - `get_session_per_model_cost(session_id: str) -> list[dict]`
  - `get_session_tool_events(session_id: str) -> list[dict]`
  - `get_session_cost_series(session_id: str) -> list[dict]`
  - `get_tools(from_date, to_date, user, team) -> list[dict]`
  - `get_users_cost(from_date, to_date, team) -> list[dict]`
  - `get_users_sessions(from_date, to_date) -> list[dict]`

- [ ] **Step 1: Write the failing tests**

```python
# tests/codemie/repository/test_coding_agent_analytics_repository.py
# Copyright 2026 EPAM Systems, Inc. ("EPAM")
#
# Licensed under the Apache License, Version 2.0 (the "License")
from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock

import pytest

from codemie.repository.coding_agent_analytics_repository import CodingAgentAnalyticsRepository


@pytest.fixture
def mock_q():
    return AsyncMock(return_value=[])


@pytest.fixture
def repo(mock_q):
    return CodingAgentAnalyticsRepository(query_fn=mock_q)


@pytest.mark.asyncio
async def test_get_root_scalars_returns_first_row(repo, mock_q):
    mock_q.return_value = [{"distinct_users": 5, "distinct_teams": 2, "total_cost_usd": 100.0,
                             "date_range_min": "2026-06-01", "date_range_max": "2026-07-20"}]
    result = await repo.get_root_scalars()
    assert result["distinct_users"] == 5
    mock_q.assert_called_once()


@pytest.mark.asyncio
async def test_get_root_scalars_returns_empty_dict_when_no_rows(repo, mock_q):
    mock_q.return_value = []
    result = await repo.get_root_scalars()
    assert result == {}


@pytest.mark.asyncio
async def test_get_cost_rows_includes_user_filter_in_params(repo, mock_q):
    mock_q.return_value = []
    await repo.get_cost_rows(date(2026, 6, 1), date(2026, 7, 20), user="dev@x.com", team=None, model=None)
    call_args = mock_q.call_args
    params = call_args[0][1]
    assert params.get("user") == "dev@x.com"


@pytest.mark.asyncio
async def test_get_cost_rows_no_optional_filters_when_none(repo, mock_q):
    mock_q.return_value = []
    await repo.get_cost_rows(date(2026, 6, 1), date(2026, 7, 20), user=None, team=None, model=None)
    call_args = mock_q.call_args
    params = call_args[0][1]
    assert "user" not in params
    assert "team" not in params
    assert "model" not in params


@pytest.mark.asyncio
async def test_get_sessions_page_passes_limit_offset(repo, mock_q):
    mock_q.return_value = []
    await repo.get_sessions_page(date(2026, 6, 1), date(2026, 7, 20), None, None, limit=10, offset=20)
    call_args = mock_q.call_args
    params = call_args[0][1]
    assert params["limit"] == 10
    assert params["offset"] == 20


@pytest.mark.asyncio
async def test_get_sessions_count_returns_integer(repo, mock_q):
    mock_q.return_value = [{"count": 42}]
    count = await repo.get_sessions_count(date(2026, 6, 1), date(2026, 7, 20), None, None)
    assert count == 42


@pytest.mark.asyncio
async def test_get_sessions_count_returns_zero_when_no_rows(repo, mock_q):
    mock_q.return_value = []
    count = await repo.get_sessions_count(date(2026, 6, 1), date(2026, 7, 20), None, None)
    assert count == 0


@pytest.mark.asyncio
async def test_get_per_model_costs_passes_session_ids(repo, mock_q):
    mock_q.return_value = []
    await repo.get_per_model_costs(["sid1", "sid2"])
    call_args = mock_q.call_args
    params = call_args[0][1]
    assert params["session_ids"] == ["sid1", "sid2"]


@pytest.mark.asyncio
async def test_get_session_by_id_returns_first_row_or_none(repo, mock_q):
    mock_q.return_value = [{"session_id": "abc", "cost_usd": 1.5}]
    result = await repo.get_session_by_id("abc")
    assert result is not None
    assert result["session_id"] == "abc"

    mock_q.return_value = []
    result = await repo.get_session_by_id("missing")
    assert result is None


@pytest.mark.asyncio
async def test_get_tools_rows_includes_user_filter(repo, mock_q):
    mock_q.return_value = []
    await repo.get_tools_rows(date(2026, 6, 1), date(2026, 7, 20), user="dev@x.com", team=None)
    params = mock_q.call_args[0][1]
    assert params.get("user") == "dev@x.com"
```

- [ ] **Step 2: Run tests — expect ImportError**

```bash
poetry run pytest tests/codemie/repository/test_coding_agent_analytics_repository.py -v
```

Expected: `ImportError: cannot import name 'CodingAgentAnalyticsRepository'`

- [ ] **Step 3: Create `src/codemie/repository/coding_agent_analytics_repository.py`**

```python
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
from __future__ import annotations

from collections.abc import Callable, Coroutine
from datetime import date
from typing import Any


class CodingAgentAnalyticsRepository:
    QueryFn = Callable[[str, dict], Awaitable[list[dict]]]

    def __init__(self, query_fn: QueryFn) -> None:
        self._q = query_fn

    # ── Root / filter options ──────────────────────────────────────────────

    async def get_root_scalars(self) -> dict:
        sql = """
SELECT
    count(DISTINCT user_email)  AS distinct_users,
    count(DISTINCT team_name)   AS distinct_teams,
    sum(cost_usd)               AS total_cost_usd,
    min(day)                    AS date_range_min,
    max(day)                    AS date_range_max
FROM codemie_analytics.coding_agent_cost_daily
WHERE user_email != ''
"""
        rows = await self._q(sql, {})
        return rows[0] if rows else {}

    async def get_root_filter_lists(self) -> dict:
        sql = """
SELECT
    groupUniqArray(user_email)  AS users,
    groupUniqArray(team_name)   AS teams,
    groupUniqArray(model_name)  AS models
FROM codemie_analytics.coding_agent_cost_daily
WHERE user_email != '' AND team_name != '' AND model_name != ''
"""
        rows = await self._q(sql, {})
        return rows[0] if rows else {"users": [], "teams": [], "models": []}

    # ── Cost ──────────────────────────────────────────────────────────────

    async def get_cost_rows(
        self,
        from_date: date,
        to_date: date,
        user: str | None,
        team: str | None,
        model: str | None,
    ) -> list[dict]:
        conditions = [
            "day BETWEEN {from_date:Date} AND {to_date:Date}",
            "user_email != ''",
        ]
        params: dict = {"from_date": from_date, "to_date": to_date}
        if user:
            conditions.append("user_email = {user:String}")
            params["user"] = user
        if team:
            conditions.append("team_name = {team:String}")
            params["team"] = team
        if model:
            conditions.append("model_name = {model:String}")
            params["model"] = model
        where = " AND ".join(conditions)
        sql = f"""
SELECT
    day,
    user_email,
    team_name,
    model_name,
    query_source,
    sum(cost_usd)               AS cost_usd,
    sum(input_tokens)           AS input_tokens,
    sum(output_tokens)          AS output_tokens,
    sum(cache_read_tokens)      AS cache_read_tokens,
    sum(cache_creation_tokens)  AS cache_creation_tokens,
    sum(api_call_count)         AS api_call_count
FROM codemie_analytics.coding_agent_cost_daily
WHERE {where}
GROUP BY day, user_email, team_name, model_name, query_source
ORDER BY day DESC, user_email, model_name
"""
        return await self._q(sql, params)

    # ── Sessions ──────────────────────────────────────────────────────────

    _SESSIONS_CTE = """
WITH
    starts AS (
        SELECT
            session_id,
            developer_name,
            team_name,
            anyLast(cwd)             AS cwd,
            anyLast(git_branch)      AS git_branch,
            anyLast(permission_mode) AS permission_mode,
            min(Timestamp)           AS started_at
        FROM codemie_analytics.coding_agent_hook_events
        WHERE event_type = 'agent.session.start'
          AND TimestampDate BETWEEN {{from_date:Date}} AND {{to_date:Date}}
          {user_filter}
          {team_filter}
        GROUP BY session_id, developer_name, team_name
    ),
    stops AS (
        SELECT session_id, max(Timestamp) AS ended_at
        FROM codemie_analytics.coding_agent_hook_events
        WHERE event_type = 'agent.session.stop'
          AND session_id IN (SELECT session_id FROM starts)
        GROUP BY session_id
    ),
    first_prompts AS (
        SELECT
            session_id,
            argMin(prompt_body, turn_number) AS first_prompt
        FROM codemie_analytics.coding_agent_hook_events
        WHERE event_type = 'agent.prompt.submit'
          AND prompt_body != ''
          AND session_id IN (SELECT session_id FROM starts)
        GROUP BY session_id
    ),
    turns AS (
        SELECT session_id, count() AS turn_count
        FROM codemie_analytics.coding_agent_hook_events
        WHERE event_type = 'agent.prompt.submit'
          AND session_id IN (SELECT session_id FROM starts)
        GROUP BY session_id
    ),
    tool_counts AS (
        SELECT
            session_id,
            countIf(event_type = 'agent.tool.start') AS tool_calls_total,
            countIf(event_type = 'agent.tool.error') AS tool_calls_failure
        FROM codemie_analytics.coding_agent_hook_events
        WHERE event_type IN ('agent.tool.start', 'agent.tool.end', 'agent.tool.error')
          AND session_id IN (SELECT session_id FROM starts)
        GROUP BY session_id
    ),
    cost AS (
        SELECT
            session_id,
            any(user_email)                                              AS user_email,
            sum(toFloat64OrZero(LogAttributes['cost_usd']))              AS cost_usd,
            sum(toUInt64OrZero(LogAttributes['input_tokens']))           AS input_tokens,
            sum(toUInt64OrZero(LogAttributes['output_tokens']))          AS output_tokens,
            sum(toUInt64OrZero(LogAttributes['cache_read_tokens']))      AS cache_read_tokens,
            sum(toUInt64OrZero(LogAttributes['cache_creation_tokens']))  AS cache_creation_tokens,
            toUnixTimestamp64Milli(min(Timestamp))                       AS min_ts_ms,
            toUnixTimestamp64Milli(max(Timestamp))                       AS max_ts_ms,
            groupUniqArray(model_name)                                   AS model_names
        FROM codemie_analytics.coding_agent_logs
        WHERE event_name = 'api_request'
          AND session_id IN (SELECT session_id FROM starts)
        GROUP BY session_id
    )
SELECT
    s.session_id,
    s.developer_name,
    s.team_name,
    s.cwd,
    s.git_branch,
    s.permission_mode,
    coalesce(c.min_ts_ms,
             toUnixTimestamp64Milli(s.started_at))                    AS start_time_ms,
    greatest(0, coalesce(
        greatest(c.max_ts_ms, toUnixTimestamp64Milli(stop.ended_at)),
        c.max_ts_ms,
        toUnixTimestamp64Milli(stop.ended_at),
        toUnixTimestamp64Milli(s.started_at)
    ) - coalesce(c.min_ts_ms, toUnixTimestamp64Milli(s.started_at))) AS duration_ms,
    coalesce(fp.first_prompt, '')                                      AS first_prompt,
    coalesce(t.turn_count, 0)                                          AS turns,
    coalesce(tc.tool_calls_total, 0)                                   AS tool_calls_total,
    coalesce(tc.tool_calls_failure, 0)                                 AS tool_calls_failure,
    coalesce(c.user_email, '')                                         AS user_email,
    coalesce(c.cost_usd, 0.0)                                          AS cost_usd,
    coalesce(c.input_tokens, 0)                                        AS input_tokens,
    coalesce(c.output_tokens, 0)                                       AS output_tokens,
    coalesce(c.cache_read_tokens, 0)                                   AS cache_read_tokens,
    coalesce(c.cache_creation_tokens, 0)                               AS cache_creation_tokens,
    coalesce(c.model_names, [])                                        AS model_names
FROM starts s
LEFT JOIN stops stop       ON s.session_id = stop.session_id
LEFT JOIN first_prompts fp ON s.session_id = fp.session_id
LEFT JOIN turns t          ON s.session_id = t.session_id
LEFT JOIN tool_counts tc   ON s.session_id = tc.session_id
LEFT JOIN cost c           ON s.session_id = c.session_id
"""

    def _build_sessions_params(
        self, from_date: date, to_date: date, user: str | None, team: str | None
    ) -> tuple[str, str, dict]:
        user_filter = "AND developer_name = {user:String}" if user else ""
        team_filter = "AND team_name = {team:String}" if team else ""
        params: dict = {"from_date": from_date, "to_date": to_date}
        if user:
            params["user"] = user
        if team:
            params["team"] = team
        return user_filter, team_filter, params

    async def get_sessions_page(
        self,
        from_date: date,
        to_date: date,
        user: str | None,
        team: str | None,
        limit: int,
        offset: int,
    ) -> list[dict]:
        user_filter, team_filter, params = self._build_sessions_params(from_date, to_date, user, team)
        params["limit"] = limit
        params["offset"] = offset
        sql = (
            self._SESSIONS_CTE.format(user_filter=user_filter, team_filter=team_filter)
            + "\nORDER BY start_time_ms DESC\nLIMIT {limit:UInt32}\nOFFSET {offset:UInt32}"
        )
        return await self._q(sql, params)

    async def get_sessions_count(
        self,
        from_date: date,
        to_date: date,
        user: str | None,
        team: str | None,
    ) -> int:
        user_filter = "AND developer_name = {user:String}" if user else ""
        team_filter = "AND team_name = {team:String}" if team else ""
        params: dict = {"from_date": from_date, "to_date": to_date}
        if user:
            params["user"] = user
        if team:
            params["team"] = team
        sql = f"""
SELECT count(DISTINCT session_id) AS count
FROM codemie_analytics.coding_agent_hook_events
WHERE event_type = 'agent.session.start'
  AND TimestampDate BETWEEN {{from_date:Date}} AND {{to_date:Date}}
  {user_filter}
  {team_filter}
"""
        rows = await self._q(sql, params)
        return int(rows[0]["count"]) if rows else 0

    async def get_per_model_costs(self, session_ids: list[str]) -> list[dict]:
        sql = """
SELECT
    session_id,
    model_name,
    sum(toFloat64OrZero(LogAttributes['cost_usd']))             AS cost_usd,
    sum(toUInt64OrZero(LogAttributes['input_tokens']))           AS input_tokens,
    sum(toUInt64OrZero(LogAttributes['output_tokens']))          AS output_tokens,
    sum(toUInt64OrZero(LogAttributes['cache_read_tokens']))      AS cache_read_tokens,
    sum(toUInt64OrZero(LogAttributes['cache_creation_tokens']))  AS cache_creation_tokens
FROM codemie_analytics.coding_agent_logs
WHERE event_name = 'api_request'
  AND session_id IN {session_ids:Array(String)}
GROUP BY session_id, model_name
"""
        return await self._q(sql, {"session_ids": session_ids})

    async def get_per_session_tool_stats(self, session_ids: list[str]) -> list[dict]:
        sql = """
SELECT
    session_id,
    tool_name,
    countIf(event_type = 'agent.tool.start') AS total_calls,
    countIf(event_type = 'agent.tool.end')   AS success_count,
    countIf(event_type = 'agent.tool.error') AS failure_count
FROM codemie_analytics.coding_agent_hook_events
WHERE session_id IN {session_ids:Array(String)}
  AND tool_name != ''
  AND event_type IN ('agent.tool.start', 'agent.tool.end', 'agent.tool.error')
GROUP BY session_id, tool_name
"""
        return await self._q(sql, {"session_ids": session_ids})

    async def get_session_by_id(self, session_id: str) -> dict | None:
        user_filter = "AND developer_name = {user:String}" if False else ""
        team_filter = ""
        # Reuse CTE but filter by single session_id instead of date range
        sql = """
WITH
    starts AS (
        SELECT
            session_id,
            developer_name,
            team_name,
            anyLast(cwd)             AS cwd,
            anyLast(git_branch)      AS git_branch,
            anyLast(permission_mode) AS permission_mode,
            min(Timestamp)           AS started_at
        FROM codemie_analytics.coding_agent_hook_events
        WHERE event_type = 'agent.session.start'
          AND session_id = {session_id:String}
        GROUP BY session_id, developer_name, team_name
    ),
    stops AS (
        SELECT session_id, max(Timestamp) AS ended_at
        FROM codemie_analytics.coding_agent_hook_events
        WHERE event_type = 'agent.session.stop'
          AND session_id IN (SELECT session_id FROM starts)
        GROUP BY session_id
    ),
    first_prompts AS (
        SELECT
            session_id,
            argMin(prompt_body, turn_number) AS first_prompt
        FROM codemie_analytics.coding_agent_hook_events
        WHERE event_type = 'agent.prompt.submit'
          AND prompt_body != ''
          AND session_id IN (SELECT session_id FROM starts)
        GROUP BY session_id
    ),
    turns AS (
        SELECT session_id, count() AS turn_count
        FROM codemie_analytics.coding_agent_hook_events
        WHERE event_type = 'agent.prompt.submit'
          AND session_id IN (SELECT session_id FROM starts)
        GROUP BY session_id
    ),
    tool_counts AS (
        SELECT
            session_id,
            countIf(event_type = 'agent.tool.start') AS tool_calls_total,
            countIf(event_type = 'agent.tool.error') AS tool_calls_failure
        FROM codemie_analytics.coding_agent_hook_events
        WHERE event_type IN ('agent.tool.start', 'agent.tool.end', 'agent.tool.error')
          AND session_id IN (SELECT session_id FROM starts)
        GROUP BY session_id
    ),
    cost AS (
        SELECT
            session_id,
            any(user_email)                                              AS user_email,
            sum(toFloat64OrZero(LogAttributes['cost_usd']))              AS cost_usd,
            sum(toUInt64OrZero(LogAttributes['input_tokens']))           AS input_tokens,
            sum(toUInt64OrZero(LogAttributes['output_tokens']))          AS output_tokens,
            sum(toUInt64OrZero(LogAttributes['cache_read_tokens']))      AS cache_read_tokens,
            sum(toUInt64OrZero(LogAttributes['cache_creation_tokens']))  AS cache_creation_tokens,
            toUnixTimestamp64Milli(min(Timestamp))                       AS min_ts_ms,
            toUnixTimestamp64Milli(max(Timestamp))                       AS max_ts_ms,
            groupUniqArray(model_name)                                   AS model_names
        FROM codemie_analytics.coding_agent_logs
        WHERE event_name = 'api_request'
          AND session_id IN (SELECT session_id FROM starts)
        GROUP BY session_id
    )
SELECT
    s.session_id,
    s.developer_name,
    s.team_name,
    s.cwd,
    s.git_branch,
    s.permission_mode,
    coalesce(c.min_ts_ms, toUnixTimestamp64Milli(s.started_at))  AS start_time_ms,
    greatest(0, coalesce(
        greatest(c.max_ts_ms, toUnixTimestamp64Milli(stop.ended_at)),
        c.max_ts_ms,
        toUnixTimestamp64Milli(stop.ended_at),
        toUnixTimestamp64Milli(s.started_at)
    ) - coalesce(c.min_ts_ms, toUnixTimestamp64Milli(s.started_at))) AS duration_ms,
    coalesce(fp.first_prompt, '')   AS first_prompt,
    coalesce(t.turn_count, 0)       AS turns,
    coalesce(tc.tool_calls_total, 0)  AS tool_calls_total,
    coalesce(tc.tool_calls_failure, 0) AS tool_calls_failure,
    coalesce(c.user_email, '')      AS user_email,
    coalesce(c.cost_usd, 0.0)       AS cost_usd,
    coalesce(c.input_tokens, 0)     AS input_tokens,
    coalesce(c.output_tokens, 0)    AS output_tokens,
    coalesce(c.cache_read_tokens, 0) AS cache_read_tokens,
    coalesce(c.cache_creation_tokens, 0) AS cache_creation_tokens,
    coalesce(c.model_names, [])     AS model_names
FROM starts s
LEFT JOIN stops stop       ON s.session_id = stop.session_id
LEFT JOIN first_prompts fp ON s.session_id = fp.session_id
LEFT JOIN turns t          ON s.session_id = t.session_id
LEFT JOIN tool_counts tc   ON s.session_id = tc.session_id
LEFT JOIN cost c           ON s.session_id = c.session_id
"""
        rows = await self._q(sql, {"session_id": session_id})
        return rows[0] if rows else None

    async def get_session_tool_events(self, session_id: str) -> list[dict]:
        sql = """
SELECT
    toUnixTimestamp64Milli(Timestamp) AS timestamp_ms,
    event_type,
    tool_name,
    tool_use_id,
    tool_input,
    tool_output,
    error_message
FROM codemie_analytics.coding_agent_hook_events
WHERE session_id = {session_id:String}
  AND event_type IN ('agent.tool.start', 'agent.tool.end', 'agent.tool.error')
ORDER BY Timestamp
"""
        return await self._q(sql, {"session_id": session_id})

    async def get_session_cost_series(self, session_id: str) -> list[dict]:
        sql = """
SELECT
    event_sequence                                             AS t,
    toFloat64OrZero(LogAttributes['cost_usd'])                AS cost_this_call,
    (toUInt64OrZero(LogAttributes['input_tokens'])
     + toUInt64OrZero(LogAttributes['output_tokens'])
     + toUInt64OrZero(LogAttributes['cache_read_tokens'])
     + toUInt64OrZero(LogAttributes['cache_creation_tokens'])) AS tokens_this_call
FROM codemie_analytics.coding_agent_logs
WHERE event_name = 'api_request'
  AND session_id = {session_id:String}
ORDER BY event_sequence
"""
        return await self._q(sql, {"session_id": session_id})

    # ── Tools ─────────────────────────────────────────────────────────────

    async def get_tools_rows(
        self,
        from_date: date,
        to_date: date,
        user: str | None,
        team: str | None,
    ) -> list[dict]:
        conditions = [
            "event_type IN ('agent.tool.start', 'agent.tool.end', 'agent.tool.error')",
            "tool_name != ''",
            "TimestampDate BETWEEN {from_date:Date} AND {to_date:Date}",
        ]
        params: dict = {"from_date": from_date, "to_date": to_date}
        if user:
            conditions.append("developer_name = {user:String}")
            params["user"] = user
        if team:
            conditions.append("team_name = {team:String}")
            params["team"] = team
        where = " AND ".join(conditions)
        sql = f"""
SELECT
    developer_name,
    team_name,
    tool_name,
    countIf(event_type = 'agent.tool.start') AS total_calls,
    countIf(event_type = 'agent.tool.end')   AS success_count,
    countIf(event_type = 'agent.tool.error') AS failure_count
FROM codemie_analytics.coding_agent_hook_events
WHERE {where}
GROUP BY developer_name, team_name, tool_name
ORDER BY total_calls DESC
"""
        return await self._q(sql, params)

    # ── Users ─────────────────────────────────────────────────────────────

    async def get_users_cost_rows(
        self,
        from_date: date,
        to_date: date,
        team: str | None,
    ) -> list[dict]:
        conditions = [
            "day BETWEEN {from_date:Date} AND {to_date:Date}",
            "user_email != ''",
        ]
        params: dict = {"from_date": from_date, "to_date": to_date}
        if team:
            conditions.append("team_name = {team:String}")
            params["team"] = team
        where = " AND ".join(conditions)
        sql = f"""
SELECT
    user_email,
    team_name,
    sum(cost_usd)               AS total_cost_usd,
    sum(api_call_count)         AS total_api_calls,
    sum(input_tokens)           AS total_input_tokens,
    sum(output_tokens)          AS total_output_tokens,
    sum(cache_read_tokens)      AS total_cache_read_tokens,
    sum(cache_creation_tokens)  AS total_cache_creation_tokens
FROM codemie_analytics.coding_agent_cost_daily
WHERE {where}
GROUP BY user_email, team_name
ORDER BY total_cost_usd DESC
"""
        return await self._q(sql, params)

    async def get_users_sessions_rows(self, from_date: date, to_date: date) -> list[dict]:
        sql = """
SELECT
    user_email,
    count(DISTINCT session_id)             AS total_sessions,
    toUnixTimestamp64Milli(max(Timestamp)) AS last_active_ms
FROM codemie_analytics.coding_agent_logs
WHERE event_name = 'api_request'
  AND TimestampDate BETWEEN {from_date:Date} AND {to_date:Date}
  AND user_email != ''
GROUP BY user_email
"""
        return await self._q(sql, {"from_date": from_date, "to_date": to_date})
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
poetry run pytest tests/codemie/repository/test_coding_agent_analytics_repository.py -v
```

Expected: `11 passed`

- [ ] **Step 5: Commit**

```bash
git add src/codemie/repository/coding_agent_analytics_repository.py tests/codemie/repository/test_coding_agent_analytics_repository.py
git commit -m "feat(EPMCDME-13561): add ClickHouse repository with all 13 query methods"
```

---

### Task 5: Service Handler

**Files:**
- Create: `src/codemie/service/analytics/handlers/coding_agents_handler.py`
- Create: `tests/codemie/service/analytics/handlers/test_coding_agents_handler.py`

**Interfaces:**
- Consumes:
  - `CodingAgentAnalyticsRepository` from Task 4 (pricing module removed — Task 3)
  - All model classes from Task 2
- Produces: `CodingAgentsHandler` class with async methods returning `dict` for `_create_response`:
  - `get_root() -> dict`
  - `get_cost(from_date, to_date, user, team, model) -> dict`
  - `get_sessions(from_date, to_date, user, team, limit, offset) -> dict`
  - `get_session_detail(session_id: str) -> dict` (raises `ExtendedHTTPException` 404 if missing)
  - `get_tools(from_date, to_date, user, team) -> dict`
  - `get_users(from_date, to_date, team) -> dict`

- [ ] **Step 1: Write the failing tests**

```python
# tests/codemie/service/analytics/handlers/test_coding_agents_handler.py
# Copyright 2026 EPAM Systems, Inc. ("EPAM")
#
# Licensed under the Apache License, Version 2.0 (the "License")
from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pytest

from codemie.service.analytics.handlers.coding_agents_handler import CodingAgentsHandler


@pytest.fixture
def mock_repo():
    repo = MagicMock()
    repo.get_root_scalars = AsyncMock(return_value={
        "distinct_users": 3, "distinct_teams": 2, "total_cost_usd": 100.0,
        "date_range_min": "2026-06-01", "date_range_max": "2026-07-20",
    })
    repo.get_root_filter_lists = AsyncMock(return_value={
        "users": ["a@x.com"], "teams": ["backend"], "models": ["claude-sonnet-4-6"],
    })
    repo.get_cost_rows = AsyncMock(return_value=[])
    repo.get_sessions_page = AsyncMock(return_value=[])
    repo.get_sessions_count = AsyncMock(return_value=0)
    repo.get_per_model_costs = AsyncMock(return_value=[])
    repo.get_per_session_tool_stats = AsyncMock(return_value=[])
    repo.get_session_by_id = AsyncMock(return_value=None)
    repo.get_session_tool_events = AsyncMock(return_value=[])
    repo.get_session_cost_series = AsyncMock(return_value=[])
    repo.get_tools_rows = AsyncMock(return_value=[])
    repo.get_users_cost_rows = AsyncMock(return_value=[])
    repo.get_users_sessions_rows = AsyncMock(return_value=[])
    return repo


@pytest.fixture
def handler(mock_repo):
    return CodingAgentsHandler(repository=mock_repo)


FROM = date(2026, 6, 20)
TO = date(2026, 7, 20)


@pytest.mark.asyncio
async def test_get_root_assembles_response(handler, mock_repo):
    result = await handler.get_root()
    assert result["distinct_users"] == 3
    assert result["agents"] == ["claude-code"]
    assert result["projects"] == []


@pytest.mark.asyncio
async def test_get_cost_returns_totals(handler, mock_repo):
    mock_repo.get_cost_rows.return_value = [
        {"day": "2026-07-20", "user_email": "dev@x.com", "team_name": "backend",
         "model_name": "claude-sonnet-4-6", "query_source": "repl",
         "cost_usd": 1.5, "input_tokens": 1000, "output_tokens": 500,
         "cache_read_tokens": 200, "cache_creation_tokens": 100, "api_call_count": 3},
    ]
    result = await handler.get_cost(FROM, TO, None, None, None)
    assert result["total_cost_usd"] == 1.5
    assert result["total_api_calls"] == 3
    assert len(result["data"]) == 1


@pytest.mark.asyncio
async def test_get_sessions_derives_title_strips_xml_tag(handler, mock_repo):
    mock_repo.get_sessions_page.return_value = [
        _make_session_row(first_prompt="<user_message>Fix the authentication bug in the login flow"),
    ]
    result = await handler.get_sessions(FROM, TO, None, None, 500, 0)
    session = result["sessions"][0]
    assert session["title"] == "Fix the authentication bug in the login flow"


@pytest.mark.asyncio
async def test_get_sessions_title_capped_at_10_words(handler, mock_repo):
    mock_repo.get_sessions_page.return_value = [
        _make_session_row(first_prompt="one two three four five six seven eight nine ten eleven twelve"),
    ]
    result = await handler.get_sessions(FROM, TO, None, None, 500, 0)
    title = result["sessions"][0]["title"]
    assert len(title.split()) == 10


@pytest.mark.asyncio
async def test_get_sessions_attaches_per_model_cost(handler, mock_repo):
    session_row = _make_session_row()
    mock_repo.get_sessions_page.return_value = [session_row]
    mock_repo.get_per_model_costs.return_value = [
        {"session_id": "sid1", "model_name": "claude-sonnet-4-6",
         "cost_usd": 1.5, "input_tokens": 1000, "output_tokens": 500,
         "cache_read_tokens": 200, "cache_creation_tokens": 50},
    ]
    result = await handler.get_sessions(FROM, TO, None, None, 500, 0)
    session = result["sessions"][0]
    assert len(session["per_model_cost"]) == 1
    assert session["per_model_cost"][0]["model_name"] == "claude-sonnet-4-6"


@pytest.mark.asyncio
async def test_get_sessions_computes_cache_read_cost_usd(handler, mock_repo):
    session_row = _make_session_row()
    mock_repo.get_sessions_page.return_value = [session_row]
    # claude-sonnet-4 rate: cache_read = 0.3/1M tokens; 1_000_000 → 0.3 USD
    mock_repo.get_per_model_costs.return_value = [
        {"session_id": "sid1", "model_name": "claude-sonnet-4",
         "cost_usd": 0.3, "input_tokens": 0, "output_tokens": 0,
         "cache_read_tokens": 1_000_000, "cache_creation_tokens": 0},
    ]
    result = await handler.get_sessions(FROM, TO, None, None, 500, 0)
    session = result["sessions"][0]
    assert abs(session["cache_read_cost_usd"] - 0.3) < 1e-6


@pytest.mark.asyncio
async def test_get_sessions_attaches_tool_stats_with_success_rate(handler, mock_repo):
    mock_repo.get_sessions_page.return_value = [_make_session_row()]
    mock_repo.get_per_session_tool_stats.return_value = [
        {"session_id": "sid1", "tool_name": "Bash", "total_calls": 10,
         "success_count": 9, "failure_count": 1},
    ]
    result = await handler.get_sessions(FROM, TO, None, None, 500, 0)
    tools = result["sessions"][0]["tools"]
    assert len(tools) == 1
    assert tools[0]["success_rate"] == 90.0


@pytest.mark.asyncio
async def test_get_sessions_sets_v1_stub_fields(handler, mock_repo):
    mock_repo.get_sessions_page.return_value = [_make_session_row()]
    result = await handler.get_sessions(FROM, TO, None, None, 500, 0)
    s = result["sessions"][0]
    assert s["active_ms"] is None
    assert s["file_ops"] == 0
    assert s["languages"] == []
    assert s["agent_invocations"] == []


@pytest.mark.asyncio
async def test_get_session_detail_raises_404_when_not_found(handler, mock_repo):
    from fastapi import status
    from codemie.core.exceptions import ExtendedHTTPException
    mock_repo.get_session_by_id.return_value = None
    with pytest.raises(ExtendedHTTPException) as exc_info:
        await handler.get_session_detail("missing-id")
    assert exc_info.value.code == status.HTTP_404_NOT_FOUND


@pytest.mark.asyncio
async def test_get_session_detail_builds_cumulative_cost_series(handler, mock_repo):
    mock_repo.get_session_by_id.return_value = _make_session_row()
    mock_repo.get_session_cost_series.return_value = [
        {"t": 1, "cost_this_call": 0.5, "tokens_this_call": 1000},
        {"t": 2, "cost_this_call": 0.3, "tokens_this_call": 500},
    ]
    result = await handler.get_session_detail("sid1")
    series = result["cost_series"]
    assert series[0]["cost"] == pytest.approx(0.5)
    assert series[1]["cost"] == pytest.approx(0.8)  # cumulative
    assert series[1]["tokens"] == 1500              # cumulative


@pytest.mark.asyncio
async def test_get_tools_computes_success_rate(handler, mock_repo):
    mock_repo.get_tools_rows.return_value = [
        {"developer_name": "dev@x.com", "team_name": "backend", "tool_name": "Bash",
         "total_calls": 20, "success_count": 18, "failure_count": 2},
    ]
    result = await handler.get_tools(FROM, TO, None, None)
    assert result["tools"][0]["success_rate"] == 90.0
    assert result["total_tool_calls"] == 20


@pytest.mark.asyncio
async def test_get_users_merges_cost_and_session_rows(handler, mock_repo):
    mock_repo.get_users_cost_rows.return_value = [
        {"user_email": "dev@x.com", "team_name": "backend",
         "total_cost_usd": 50.0, "total_api_calls": 100,
         "total_input_tokens": 1000, "total_output_tokens": 500,
         "total_cache_read_tokens": 200, "total_cache_creation_tokens": 100},
    ]
    mock_repo.get_users_sessions_rows.return_value = [
        {"user_email": "dev@x.com", "total_sessions": 5, "last_active_ms": 1753001600000},
    ]
    result = await handler.get_users(FROM, TO, None)
    user = result["users"][0]
    assert user["total_sessions"] == 5
    assert user["total_tokens"] == 1800  # 1000+500+200+100
    assert user["last_active_ms"] == 1753001600000


def _make_session_row(first_prompt: str = "Fix the auth bug") -> dict:
    return {
        "session_id": "sid1",
        "developer_name": "dev@x.com",
        "team_name": "backend",
        "cwd": "/home/dev/proj",
        "git_branch": "feature/auth",
        "permission_mode": "default",
        "start_time_ms": 1753001600000,
        "duration_ms": 2_700_000,
        "first_prompt": first_prompt,
        "turns": 14,
        "tool_calls_total": 37,
        "tool_calls_failure": 2,
        "user_email": "dev@x.com",
        "cost_usd": 3.21,
        "input_tokens": 28000,
        "output_tokens": 7200,
        "cache_read_tokens": 5000,
        "cache_creation_tokens": 800,
        "model_names": ["claude-sonnet-4-6"],
    }
```

- [ ] **Step 2: Run tests — expect ImportError**

```bash
poetry run pytest tests/codemie/service/analytics/handlers/test_coding_agents_handler.py -v
```

Expected: `ImportError: cannot import name 'CodingAgentsHandler'`

- [ ] **Step 3: Create `src/codemie/service/analytics/handlers/coding_agents_handler.py`**

```python
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
from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone

from fastapi import status

from codemie.core.exceptions import ExtendedHTTPException
from codemie.repository.coding_agent_analytics_repository import CodingAgentAnalyticsRepository
from codemie.service.analytics.handlers.coding_agent_pricing import cache_read_cost


def _derive_title(first_prompt: str) -> str:
    text = first_prompt
    if text.startswith("<"):
        gt = text.find(">")
        if gt > 1:
            tag_name = text[1:gt].split()[0]
            close = f"</{tag_name}>"
            end = text.find(close, gt + 1)
            if end != -1:
                text = text[end + len(close) :]
    prompt = text.strip()
    words = prompt.split()[:10]
    return " ".join(words)[:120]


def _build_token_usage(row: dict) -> dict:
    inp = int(row.get("input_tokens", 0))
    out = int(row.get("output_tokens", 0))
    cr = int(row.get("cache_read_tokens", 0))
    cc = int(row.get("cache_creation_tokens", 0))
    return {"input": inp, "output": out, "cache_read": cr, "cache_creation": cc, "total": inp + out + cr + cc}


def _build_model_cost(mc_row: dict) -> dict:
    tokens = _build_token_usage(mc_row)
    return {
        "model_name": mc_row["model_name"],
        "tokens": tokens,
        "cost_usd": float(mc_row.get("cost_usd", 0.0)),
        "unpriced": abs(float(mc_row.get("cost_usd", 0.0))) < 1e-9,
    }


def _compute_cache_read_cost_usd(per_model: list[dict]) -> float:
    return sum(cache_read_cost(m["model_name"], m["tokens"]["cache_read"]) for m in per_model)


def _build_session_summary(
    row: dict,
    per_model: list[dict],
    tool_stats: list[dict],
) -> dict:
    tokens = _build_token_usage(row)
    per_model_cost = [_build_model_cost(m) for m in per_model]
    crcu = _compute_cache_read_cost_usd(per_model_cost)
    cost_usd = float(row.get("cost_usd", 0.0))
    tool_calls_total = int(row.get("tool_calls_total", 0))
    tool_calls_failure = int(row.get("tool_calls_failure", 0))
    tools_out = []
    for ts in tool_stats:
        tc = int(ts.get("total_calls", 0))
        sc = int(ts.get("success_count", 0))
        fc = int(ts.get("failure_count", 0))
        tools_out.append({
            "tool_name": ts["tool_name"],
            "total_calls": tc,
            "success_count": sc,
            "failure_count": fc,
            "success_rate": round(sc / tc * 100, 1) if tc > 0 else 0.0,
        })
    first_prompt = row.get("first_prompt", "")
    return {
        "session_id": row["session_id"],
        "user": row.get("user_email", ""),
        "developer_name": row.get("developer_name", ""),
        "agent_name": "claude-code",
        "provider": "anthropic",
        "team_name": row.get("team_name", ""),
        "project": row.get("cwd", ""),
        "branch": row.get("git_branch", ""),
        "cwd": row.get("cwd", ""),
        "permission_mode": row.get("permission_mode", ""),
        "title": _derive_title(first_prompt),
        "first_prompt": first_prompt,
        "start_time": int(row.get("start_time_ms", 0)),
        "duration_ms": int(row.get("duration_ms", 0)),
        "active_ms": None,
        "turns": int(row.get("turns", 0)),
        "file_ops": 0, "lines_added": 0, "lines_removed": 0, "lines_modified": 0, "net_lines": 0,
        "files_changed": 0, "files_written": 0, "files_edited": 0,
        "tool_calls_total": tool_calls_total,
        "tool_calls_success": tool_calls_total - tool_calls_failure,
        "tool_calls_failure": tool_calls_failure,
        "tools": tools_out,
        "models": list(row.get("model_names", [])),
        "languages": [],
        "tokens": tokens,
        "cost_usd": cost_usd,
        "cache_read_cost_usd": crcu,
        "per_model_cost": per_model_cost,
        "priced": cost_usd > 0,
        "agent_invocations": [], "skill_invocations": [], "command_invocations": [],
    }


def _build_totals(sessions: list[dict]) -> dict:
    if not sessions:
        return {"sessions": 0, "duration_ms": 0, "turns": 0, "files": 0, "net_lines": 0,
                "tool_calls_total": 0, "tool_success_rate": 0.0, "total_cost_usd": 0.0,
                "cache_read_cost_usd": 0.0, "priced_sessions": 0}
    total_tools = sum(s["tool_calls_total"] for s in sessions)
    total_failures = sum(s["tool_calls_failure"] for s in sessions)
    total_successes = total_tools - total_failures
    return {
        "sessions": len(sessions),
        "duration_ms": sum(s["duration_ms"] for s in sessions),
        "turns": sum(s["turns"] for s in sessions),
        "files": 0, "net_lines": 0,
        "tool_calls_total": total_tools,
        "tool_success_rate": round(total_successes / total_tools * 100, 1) if total_tools > 0 else 0.0,
        "total_cost_usd": sum(s["cost_usd"] for s in sessions),
        "cache_read_cost_usd": sum(s["cache_read_cost_usd"] for s in sessions),
        "priced_sessions": sum(1 for s in sessions if s["priced"]),
    }


class CodingAgentsHandler:
    def __init__(self, repo: CodingAgentAnalyticsRepository) -> None:
        self._repo = repo

    async def get_root(self) -> dict:
        scalars_rows, lists_rows = await asyncio.gather(
            self._repo.get_root_scalars(),
            self._repo.get_root_lists(),
        )
        s = scalars_rows[0] if scalars_rows else {}
        lst = lists_rows[0] if lists_rows else {}
        return {
            "distinct_users": int(s.get("distinct_users", 0)),
            "distinct_teams": int(s.get("distinct_teams", 0)),
            "total_cost_usd": float(s.get("total_cost_usd", 0.0)),
            "date_range_min": str(d) if (d := s.get("date_range_min")) else None,
            "date_range_max": str(d) if (d := s.get("date_range_max")) else None,
            "users": list(lst.get("users", [])),
            "teams": list(lst.get("teams", [])),
            "models": list(lst.get("models", [])),
            "agents": ["claude-code"],
            "projects": [],
        }

    async def get_cost(
        self,
        from_date: date,
        to_date: date,
        user: str | None,
        team: str | None,
        model: str | None,
    ) -> dict:
        rows = await self._repo.get_cost(from_date, to_date, user, team, model)
        data = [
            {
                "day": str(r["day"]),
                "user_email": r.get("user_email", ""),
                "team_name": r.get("team_name", ""),
                "model_name": r.get("model_name", ""),
                "query_source": r.get("query_source", ""),
                "cost_usd": float(r.get("cost_usd", 0.0)),
                "input_tokens": int(r.get("input_tokens", 0)),
                "output_tokens": int(r.get("output_tokens", 0)),
                "cache_read_tokens": int(r.get("cache_read_tokens", 0)),
                "cache_creation_tokens": int(r.get("cache_creation_tokens", 0)),
                "api_call_count": int(r.get("api_call_count", 0)),
            }
            for r in rows
        ]
        return {
            "data": data,
            "total_cost_usd": sum(r["cost_usd"] for r in data),
            "total_api_calls": sum(r["api_call_count"] for r in data),
        }

    async def get_sessions(
        self,
        from_date: date,
        to_date: date,
        user: str | None,
        team: str | None,
        limit: int,
        offset: int,
    ) -> dict:
        rows, total_count = await asyncio.gather(
            self._repo.get_sessions(from_date, to_date, user, team, limit, offset),
            self._repo.count_sessions(from_date, to_date, user, team),
        )
        session_ids = [r["session_id"] for r in rows]
        if session_ids:
            model_cost_rows, tool_stat_rows = await asyncio.gather(
                self._repo.get_sessions_per_model_cost(session_ids),
                self._repo.get_sessions_tool_stats(session_ids),
            )
        else:
            model_cost_rows, tool_stat_rows = [], []

        model_cost_by_session: dict[str, list] = {}
        for mc in model_cost_rows:
            model_cost_by_session.setdefault(mc["session_id"], []).append(mc)

        tool_stats_by_session: dict[str, list] = {}
        for ts in tool_stat_rows:
            tool_stats_by_session.setdefault(ts["session_id"], []).append(ts)

        sessions = [
            _build_session_summary(
                r,
                model_cost_by_session.get(r["session_id"], []),
                tool_stats_by_session.get(r["session_id"], []),
            )
            for r in rows
        ]
        totals = _build_totals(sessions)
        unpriced_models = list({
            mc["model_name"]
            for s in sessions
            for mc in s["per_model_cost"]
            if mc["unpriced"] and mc["tokens"]["total"] > 0
        })
        now_iso = datetime.now(tz=timezone.utc).isoformat()
        return {
            "meta": {
                "generated_at": now_iso,
                "range_label": f"{from_date}..{to_date}",
                "scope": "user" if user else "all",
                "user": user,
                "agents": list({s["agent_name"] for s in sessions}) or ["claude-code"],
                "projects": list({s["cwd"] for s in sessions if s["cwd"]}),
                "totals": totals,
                "unpriced_models": unpriced_models,
            },
            "sessions": sessions,
            "total_count": total_count,
        }

    async def get_session_detail(self, session_id: str) -> dict:
        rows, model_cost_rows, tool_event_rows, cost_series_rows = await asyncio.gather(
            self._repo.get_session_detail(session_id),
            self._repo.get_session_per_model_cost(session_id),
            self._repo.get_session_tool_events(session_id),
            self._repo.get_session_cost_series(session_id),
        )
        if not rows:
            raise ExtendedHTTPException(
                code=status.HTTP_404_NOT_FOUND,
                message="Session not found",
                details=f"No session with id {session_id!r}",
                help="Check the session_id and try again.",
            )
        summary = _build_session_summary(rows[0], model_cost_rows, [])
        cumulative_cost = 0.0
        cumulative_tokens = 0
        cost_series = []
        for row in cost_series_rows:
            cumulative_cost += float(row.get("cost_this_call", 0.0))
            cumulative_tokens += int(row.get("tokens_this_call", 0))
            cost_series.append({"t": int(row["t"]), "cost": cumulative_cost, "tokens": cumulative_tokens})
        return {**summary, "cost_series": cost_series, "dispatches": [], "tool_events": [dict(r) for r in tool_event_rows]}

    async def get_tools(
        self,
        from_date: date,
        to_date: date,
        user: str | None,
        team: str | None,
    ) -> dict:
        rows = await self._repo.get_tools(from_date, to_date, user, team)
        tools = [
            {
                "tool_name": r["tool_name"],
                "developer_name": r.get("developer_name", ""),
                "team_name": r.get("team_name", ""),
                "total_calls": int(r.get("total_calls", 0)),
                "success_count": int(r.get("success_count", 0)),
                "failure_count": int(r.get("failure_count", 0)),
                "success_rate": (
                    round(int(r.get("success_count", 0)) / int(r["total_calls"]) * 100, 1)
                    if int(r.get("total_calls", 0)) > 0 else 0.0
                ),
            }
            for r in rows
        ]
        return {"tools": tools, "total_tool_calls": sum(t["total_calls"] for t in tools)}

    async def get_users(
        self,
        from_date: date,
        to_date: date,
        team: str | None,
    ) -> dict:
        cost_rows, session_rows = await asyncio.gather(
            self._repo.get_users_cost(from_date, to_date, team),
            self._repo.get_users_sessions(from_date, to_date),
        )
        session_map = {r["user_email"]: r for r in session_rows}
        users = []
        for r in cost_rows:
            email = r["user_email"]
            sess = session_map.get(email, {})
            inp = int(r.get("total_input_tokens", 0))
            out = int(r.get("total_output_tokens", 0))
            cr = int(r.get("total_cache_read_tokens", 0))
            cc = int(r.get("total_cache_creation_tokens", 0))
            users.append({
                "user_email": email,
                "team_name": r.get("team_name", ""),
                "total_cost_usd": float(r.get("total_cost_usd", 0.0)),
                "total_api_calls": int(r.get("total_api_calls", 0)),
                "total_input_tokens": inp,
                "total_output_tokens": out,
                "total_tokens": inp + out + cr + cc,
                "total_sessions": int(sess.get("total_sessions", 0)),
                "turns": 0,
                "tool_calls_total": 0,
                "net_lines": 0,
                "last_active_ms": int(sess["last_active_ms"]) if sess.get("last_active_ms") else None,
            })
        return {"users": users}
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
poetry run pytest tests/codemie/service/analytics/handlers/test_coding_agents_handler.py -v
```

Expected: `13 passed`

- [ ] **Step 5: Commit**

```bash
git add src/codemie/service/analytics/handlers/coding_agents_handler.py tests/codemie/service/analytics/handlers/test_coding_agents_handler.py
git commit -m "feat(EPMCDME-13561): add CodingAgentsHandler with 6 assembly methods"
```

---

### Task 6: Router + Registration

**Files:**
- Create: `src/codemie/rest_api/routers/coding_agents_analytics.py`
- Modify: `src/codemie/rest_api/main.py` (add import + `include_router`)
- Create: `tests/codemie/rest_api/routers/test_coding_agents_analytics.py`

**Interfaces:**
- Consumes: `CodingAgentsHandler` from Task 5; `handle_analytics_errors`, `_create_response`, `authenticate` from existing code
- Produces: `router` with 6 endpoints under `/v1/analytics/coding-agents`

- [ ] **Step 1: Write the failing tests**

```python
# tests/codemie/rest_api/routers/test_coding_agents_analytics.py
# Copyright 2026 EPAM Systems, Inc. ("EPAM")
#
# Licensed under the Apache License, Version 2.0 (the "License")
from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from codemie.rest_api.routers import coding_agents_analytics


@pytest.fixture
def mock_handler():
    h = MagicMock()
    h.get_root = AsyncMock(return_value={
        "distinct_users": 2, "distinct_teams": 1, "total_cost_usd": 50.0,
        "date_range_min": "2026-06-01", "date_range_max": "2026-07-20",
        "users": ["dev@x.com"], "teams": ["backend"],
        "models": ["claude-sonnet-4-6"], "agents": ["claude-code"], "projects": [],
    })
    h.get_cost = AsyncMock(return_value={"data": [], "total_cost_usd": 0.0, "total_api_calls": 0})
    h.get_sessions = AsyncMock(return_value={
        "meta": {
            "generated_at": "2026-07-20T00:00:00Z",
            "range_label": "2026-06-20..2026-07-20",
            "scope": "all", "user": None, "agents": [], "projects": [],
            "totals": {"sessions": 0, "duration_ms": 0, "turns": 0, "files": 0,
                       "net_lines": 0, "tool_calls_total": 0, "tool_success_rate": 0.0,
                       "total_cost_usd": 0.0, "cache_read_cost_usd": 0.0, "priced_sessions": 0},
            "unpriced_models": [],
        },
        "sessions": [], "total_count": 0,
    })
    h.get_session_detail = AsyncMock(return_value={})
    h.get_tools = AsyncMock(return_value={"tools": [], "total_tool_calls": 0})
    h.get_users = AsyncMock(return_value={"users": []})
    return h


@pytest.fixture
def client(mock_handler):
    app = FastAPI()
    app.include_router(coding_agents_analytics.router)

    async def override_authenticate():
        return MagicMock()

    async def override_get_handler():
        return mock_handler

    from codemie.rest_api.security.authentication import authenticate
    app.dependency_overrides[authenticate] = override_authenticate
    app.dependency_overrides[coding_agents_analytics.get_handler] = override_get_handler
    return TestClient(app)


def test_get_root_returns_200(client):
    resp = client.get("/v1/analytics/coding-agents/")
    assert resp.status_code == 200
    data = resp.json()
    assert data["distinct_users"] == 2


def test_get_cost_returns_200(client):
    resp = client.get("/v1/analytics/coding-agents/cost")
    assert resp.status_code == 200
    assert "data" in resp.json()


def test_get_sessions_returns_200(client):
    resp = client.get("/v1/analytics/coding-agents/sessions")
    assert resp.status_code == 200
    assert "sessions" in resp.json()


def test_get_sessions_passes_pagination(client, mock_handler):
    client.get("/v1/analytics/coding-agents/sessions?limit=10&offset=20")
    call_kwargs = mock_handler.get_sessions.call_args
    assert call_kwargs[1].get("limit") == 10 or call_kwargs[0][4] == 10
    assert call_kwargs[1].get("offset") == 20 or call_kwargs[0][5] == 20


def test_get_session_detail_returns_200(client, mock_handler):
    mock_handler.get_session_detail.return_value = {
        "session_id": "abc", "user": "dev@x.com", "developer_name": "dev@x.com",
        "agent_name": "claude-code", "provider": "anthropic", "team_name": "backend",
        "project": "/proj", "branch": "main", "cwd": "/proj", "permission_mode": "default",
        "title": "Fix bug", "first_prompt": "Fix bug", "start_time": 0, "duration_ms": 0,
        "active_ms": None, "turns": 0, "file_ops": 0, "lines_added": 0, "lines_removed": 0,
        "lines_modified": 0, "net_lines": 0, "files_changed": 0, "files_written": 0,
        "files_edited": 0, "tool_calls_total": 0, "tool_calls_success": 0, "tool_calls_failure": 0,
        "tools": [], "models": [], "languages": [], "tokens": {"input": 0, "output": 0,
        "cache_read": 0, "cache_creation": 0, "total": 0}, "cost_usd": 0.0,
        "cache_read_cost_usd": 0.0, "per_model_cost": [], "priced": False,
        "agent_invocations": [], "skill_invocations": [], "command_invocations": [],
        "cost_series": [], "dispatches": [], "tool_events": [],
    }
    resp = client.get("/v1/analytics/coding-agents/sessions/abc")
    assert resp.status_code == 200


def test_get_tools_returns_200(client):
    resp = client.get("/v1/analytics/coding-agents/tools")
    assert resp.status_code == 200
    assert "tools" in resp.json()


def test_get_users_returns_200(client):
    resp = client.get("/v1/analytics/coding-agents/users")
    assert resp.status_code == 200
    assert "users" in resp.json()
```

- [ ] **Step 2: Run tests — expect ImportError**

```bash
poetry run pytest tests/codemie/rest_api/routers/test_coding_agents_analytics.py -v
```

Expected: `ImportError: cannot import name 'coding_agents_analytics' from 'codemie.rest_api.routers'`

- [ ] **Step 3: Create `src/codemie/rest_api/routers/coding_agents_analytics.py`**

```python
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
from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, field_validator, model_validator

from codemie.repository.coding_agent_analytics_repository import CodingAgentAnalyticsRepository
from codemie.rest_api.models.coding_agents_analytics import (
    AnalyticsRootResponse,
    CostResponse,
    SessionDetail,
    SessionsResponse,
    ToolsResponse,
    UsersResponse,
)
from codemie.rest_api.routers.analytics import _create_response, handle_analytics_errors
from codemie.rest_api.security.authentication import authenticate
from codemie.service.analytics.handlers.coding_agents_handler import CodingAgentsHandler


class CodingAgentsQueryParams(BaseModel):
    from_date: date | None = Query(default=None, description="Start date (default: today − 30 days)")
    to_date: date | None = Query(default=None, description="End date (default: today)")
    user: str | None = Query(default=None, max_length=256)
    team: str | None = Query(default=None, max_length=256)
    model: str | None = Query(default=None, max_length=256)

    @model_validator(mode="after")
    def set_defaults_and_validate(self) -> "CodingAgentsQueryParams":
        today = date.today()
        if self.from_date is None:
            self.from_date = today - timedelta(days=30)
        if self.to_date is None:
            self.to_date = today
        if self.from_date > self.to_date:
            raise ValueError("from_date must not exceed to_date")
        return self


def get_handler() -> CodingAgentsHandler:
    from codemie.clients.clickhouse import ch_query
    return CodingAgentsHandler(repo=CodingAgentAnalyticsRepository(query_fn=ch_query))


router = APIRouter(
    tags=["Coding Agents Analytics"],
    prefix="/v1/analytics/coding-agents",
    dependencies=[Depends(authenticate)],
)


@router.get(
    "/",
    response_model=AnalyticsRootResponse,
    summary="Coding agents filter options and summary stats",
)
@handle_analytics_errors("coding-agents root")
async def get_coding_agents_root(
    handler: CodingAgentsHandler = Depends(get_handler),
) -> JSONResponse:
    data = await handler.get_root()
    return _create_response(data, AnalyticsRootResponse)


@router.get(
    "/cost",
    response_model=CostResponse,
    summary="Daily cost rollup per user/model",
)
@handle_analytics_errors("coding-agents cost")
async def get_coding_agents_cost(
    params: CodingAgentsQueryParams = Depends(),
    handler: CodingAgentsHandler = Depends(get_handler),
) -> JSONResponse:
    data = await handler.get_cost(params.from_date, params.to_date, params.user, params.team, params.model)
    return _create_response(data, CostResponse)


@router.get(
    "/sessions",
    response_model=SessionsResponse,
    summary="Paginated session list with per-session cost and tool breakdown",
)
@handle_analytics_errors("coding-agents sessions")
async def get_coding_agents_sessions(
    params: CodingAgentsQueryParams = Depends(),
    limit: int = Query(500, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    handler: CodingAgentsHandler = Depends(get_handler),
) -> JSONResponse:
    data = await handler.get_sessions(
        params.from_date, params.to_date, params.user, params.team, limit=limit, offset=offset
    )
    return _create_response(data, SessionsResponse)


@router.get(
    "/sessions/{session_id}",
    response_model=SessionDetail,
    summary="Full session detail with cost series and raw tool events",
)
@handle_analytics_errors("coding-agents session detail")
async def get_coding_agents_session_detail(
    session_id: str,
    handler: CodingAgentsHandler = Depends(get_handler),
) -> JSONResponse:
    data = await handler.get_session_detail(session_id)
    return _create_response(data, SessionDetail)


@router.get(
    "/tools",
    response_model=ToolsResponse,
    summary="Aggregate tool usage across all sessions",
)
@handle_analytics_errors("coding-agents tools")
async def get_coding_agents_tools(
    params: CodingAgentsQueryParams = Depends(),
    handler: CodingAgentsHandler = Depends(get_handler),
) -> JSONResponse:
    data = await handler.get_tools(params.from_date, params.to_date, params.user, params.team)
    return _create_response(data, ToolsResponse)


@router.get(
    "/users",
    response_model=UsersResponse,
    summary="Per-user cost leaderboard",
)
@handle_analytics_errors("coding-agents users")
async def get_coding_agents_users(
    params: CodingAgentsQueryParams = Depends(),
    handler: CodingAgentsHandler = Depends(get_handler),
) -> JSONResponse:
    data = await handler.get_users(params.from_date, params.to_date, params.team)
    return _create_response(data, UsersResponse)
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
poetry run pytest tests/codemie/rest_api/routers/test_coding_agents_analytics.py -v
```

Expected: `7 passed`

- [ ] **Step 5: Register the router in `main.py`**

In `src/codemie/rest_api/main.py`, add to the import block (after `analytics` in the existing `from codemie.rest_api.routers import (...)` block):

```python
    coding_agents_analytics,
```

Then after `app.include_router(analytics.router)` (around line 844), add:

```python
app.include_router(coding_agents_analytics.router)
```

- [ ] **Step 6: Run all new tests together**

```bash
poetry run pytest tests/codemie/clients/test_clickhouse.py tests/codemie/rest_api/models/test_coding_agents_analytics.py tests/codemie/repository/test_coding_agent_analytics_repository.py tests/codemie/service/analytics/handlers/test_coding_agents_handler.py tests/codemie/rest_api/routers/test_coding_agents_analytics.py -v
```

Expected: `43 passed` (3 + 9 + 11 + 13 + 7)

- [ ] **Step 7: Run lint**

```bash
make ruff
```

Expected: no errors. Fix any if present, then re-run.

- [ ] **Step 8: Commit**

```bash
git add src/codemie/rest_api/routers/coding_agents_analytics.py src/codemie/rest_api/main.py tests/codemie/rest_api/routers/test_coding_agents_analytics.py
git commit -m "feat(EPMCDME-13561): add /v1/analytics/coding-agents/ router with 6 read endpoints"
```
