# Delivery Framework Detection — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `delivery_framework` field to CLI Analytics session responses that identifies the primary delivery framework used (CodeMie AI Factory, Superpowers, BMAD, OpenSpec, Spec Kit, or Pure chat), and support optional filtering by framework on the sessions list endpoint.

**Architecture:** A new pure-Python classifier module (`delivery_framework.py`) applies priority-ordered prefix rules against skill names fetched from `coding_agent_logs`. The handler fetches skill names after the existing gather, classifies each session in Python, and injects the result into the response dict. Framework filtering follows the same Python-side pattern as the existing `search` filter.

**Tech Stack:** Python 3.12, FastAPI, ClickHouse (via existing `QueryFn`), Pydantic v2, pytest + pytest-asyncio.

## Global Constraints

- No ClickHouse schema changes — `coding_agent_logs.skill_name` already exists.
- No new feature flags — `delivery_framework` is additive and backward-compatible.
- No UI changes — backend only.
- Do not commit any changes (user instruction for this session).
- All new Python files use `from __future__ import annotations` at the top.
- Test files live under `tests/codemie/` mirroring the `src/codemie/` structure.

---

## File Map

| Action | Path |
|---|---|
| Create | `src/codemie/service/analytics/delivery_framework.py` |
| Modify | `src/codemie/rest_api/models/cli_analytics.py` |
| Modify | `src/codemie/repository/cli_analytics_repository.py` |
| Modify | `src/codemie/service/analytics/handlers/cli_analytics_handler.py` |
| Modify | `src/codemie/rest_api/routers/cli_analytics.py` |
| Create | `tests/codemie/service/analytics/test_delivery_framework.py` |
| Create | `tests/codemie/repository/test_cli_analytics_skill_query.py` |
| Create | `tests/codemie/service/analytics/handlers/test_cli_analytics_handler_framework.py` |
| Modify | `tests/codemie/service/analytics/test_delivery_framework.py` |
| Create | `tests/codemie/rest_api/routers/test_cli_analytics_frameworks.py` |

---

### Task 1: Classifier module

**Files:**
- Create: `src/codemie/service/analytics/delivery_framework.py`
- Create: `tests/codemie/service/analytics/test_delivery_framework.py`

**Interfaces:**
- Produces: `classify_delivery_framework(skill_names: list[str]) -> str`

- [x] **Step 1: Write the failing tests**

Create `tests/codemie/service/analytics/test_delivery_framework.py`:

```python
from __future__ import annotations

import pytest

from codemie.service.analytics.delivery_framework import classify_delivery_framework


def test_sdlc_factory_wins_over_superpowers():
    result = classify_delivery_framework(["sdlc-factory:tech-analyst", "superpowers:brainstorming"])
    assert result == "CodeMie AI Factory"


def test_superpowers_wins_without_sdlc():
    result = classify_delivery_framework(["superpowers:brainstorming", "bmad-task"])
    assert result == "Superpowers"


def test_bmad():
    assert classify_delivery_framework(["bmad-task"]) == "BMAD"


def test_openspec():
    assert classify_delivery_framework(["opsx:something"]) == "OpenSpec"


def test_speckit():
    assert classify_delivery_framework(["speckit.something"]) == "Spec Kit"


def test_bmad_wins_over_openspec():
    result = classify_delivery_framework(["opsx:thing", "bmad-task"])
    assert result == "BMAD"


def test_pure_chat_empty_list():
    assert classify_delivery_framework([]) == "Pure chat"


def test_pure_chat_unknown_skill():
    assert classify_delivery_framework(["some-random-skill", "another-tool"]) == "Pure chat"


def test_sdlc_factory_prefix_only():
    # only the prefix matters, not the full name
    assert classify_delivery_framework(["sdlc-factory:anything"]) == "CodeMie AI Factory"
```

- [x] **Step 2: Run tests to verify they fail**

```
pytest tests/codemie/service/analytics/test_delivery_framework.py -v
```

Expected: `ModuleNotFoundError` or `ImportError` — module does not exist yet.

- [x] **Step 3: Implement the classifier**

Create `src/codemie/service/analytics/delivery_framework.py`:

```python
from __future__ import annotations

_RULES: list[tuple[str, str]] = [
    ("sdlc-factory:",  "CodeMie AI Factory"),
    ("superpowers:",   "Superpowers"),
    ("bmad-",          "BMAD"),
    ("opsx:",          "OpenSpec"),
    ("speckit.",       "Spec Kit"),
]


def classify_delivery_framework(skill_names: list[str]) -> str:
    """Return the primary delivery framework for a session based on skill name prefixes.

    Priority is positional in _RULES — first match wins. Returns "Pure chat" when
    no known prefix is found (including empty skill_names list).

    To support additional signal types in future (e.g. agent_types, file markers),
    introduce a SessionSignals dataclass carrying all inputs and update this signature.
    """
    for prefix, label in _RULES:
        if any(s.startswith(prefix) for s in skill_names):
            return label
    return "Pure chat"
```

- [x] **Step 4: Run tests to verify they pass**

```
pytest tests/codemie/service/analytics/test_delivery_framework.py -v
```

Expected: all 9 tests PASS.

---

### Task 2: Repository method

**Files:**
- Modify: `src/codemie/repository/cli_analytics_repository.py`
- Create: `tests/codemie/repository/test_cli_analytics_skill_query.py`

**Interfaces:**
- Consumes: nothing from earlier tasks
- Produces: `LocalAnalyticsRepository.get_skill_names_by_session(session_ids: list[str]) -> list[dict]`
  - Returns: `[{"session_id": str, "skill_names": list[str]}, ...]`
  - Returns `[]` immediately when `session_ids` is empty (no query fired).

- [x] **Step 1: Write the failing tests**

Create `tests/codemie/repository/test_cli_analytics_skill_query.py`:

```python
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock

from codemie.repository.cli_analytics_repository import LocalAnalyticsRepository


@pytest.mark.asyncio
async def test_get_skill_names_by_session_empty_input():
    mock_q = AsyncMock()
    repo = LocalAnalyticsRepository(mock_q)
    result = await repo.get_skill_names_by_session([])
    mock_q.assert_not_called()
    assert result == []


@pytest.mark.asyncio
async def test_get_skill_names_by_session_sql_shape():
    expected = [{"session_id": "ses-1", "skill_names": ["sdlc-factory:tech-analyst"]}]
    mock_q = AsyncMock(return_value=expected)
    repo = LocalAnalyticsRepository(mock_q)

    result = await repo.get_skill_names_by_session(["ses-1", "ses-2"])

    sql, params = mock_q.call_args[0]
    assert "coding_agent_logs" in sql
    assert "groupUniqArray(skill_name)" in sql
    assert "event_name = 'skill_activated'" in sql
    assert "skill_name != ''" in sql
    assert params["session_ids"] == ["ses-1", "ses-2"]
    assert result == expected


@pytest.mark.asyncio
async def test_get_skill_names_by_session_returns_empty_when_no_rows():
    mock_q = AsyncMock(return_value=[])
    repo = LocalAnalyticsRepository(mock_q)
    result = await repo.get_skill_names_by_session(["ses-x"])
    assert result == []
```

> **Data source note:** The query uses `coding_agent_logs.skill_name` (materialized from `LogAttributes['skill.name']`), NOT `coding_agent_traces.span_skill_name`. The traces column has ~0.4% session coverage; the logs path captures all skill invocations including slash-command activations at session start.

- [x] **Step 2: Run tests to verify they fail**

```
pytest tests/codemie/repository/test_cli_analytics_skill_query.py -v
```

Expected: `AttributeError: 'LocalAnalyticsRepository' object has no attribute 'get_skill_names_by_session'`.

- [x] **Step 3: Add the method to the repository**

In `src/codemie/repository/cli_analytics_repository.py`, add the following method inside `LocalAnalyticsRepository`, after the existing `get_invocations` method (around line 476):

```python
async def get_skill_names_by_session(self, session_ids: list[str]) -> list[dict]:
    """Distinct skill names per session from coding_agent_logs (skill_activated events).

    Uses coding_agent_logs.skill_name (materialized from LogAttributes['skill.name'])
    rather than coding_agent_traces.span_skill_name. The traces column has ~0.4%
    coverage; the logs path captures all skill invocations including slash-command
    activations. Same pattern as CodingAgentAnalyticsRepository.get_session_skill_cost.

    Accepts already-filtered session IDs from cost_facts — no date filter needed.
    Works for both bulk (sessions list) and single-session (detail) callers;
    pass [session_id] for the detail endpoint.
    """
    if not session_ids:
        return []
    sql = """
    SELECT session_id, groupUniqArray(skill_name) AS skill_names
    FROM codemie_analytics.coding_agent_logs
    WHERE session_id IN {session_ids:Array(String)}
      AND event_name = 'skill_activated'
      AND skill_name != ''
    GROUP BY session_id
    """
    return await self._q(sql, {"session_ids": session_ids})
```

- [x] **Step 4: Run tests to verify they pass**

```
pytest tests/codemie/repository/test_cli_analytics_skill_query.py -v
```

Expected: all 3 tests PASS.

---

### Task 3: Sessions list — model field, handler extension, framework filter

**Files:**
- Modify: `src/codemie/rest_api/models/cli_analytics.py` (add field to `LocalAnalyticsSessionRow`)
- Modify: `src/codemie/service/analytics/handlers/cli_analytics_handler.py` (extend `get_sessions`, add `framework` param)
- Modify: `src/codemie/rest_api/routers/cli_analytics.py` (add `framework` query param)
- Create: `tests/codemie/service/analytics/handlers/test_cli_analytics_handler_framework.py`

**Interfaces:**
- Consumes: `classify_delivery_framework(skill_names: list[str]) -> str` from Task 1
- Consumes: `LocalAnalyticsRepository.get_skill_names_by_session(session_ids)` from Task 2
- Produces: `LocalAnalyticsSessionRow.delivery_framework: str | None`
- Produces: `LocalAnalyticsHandler.get_sessions(..., framework: str | None = None)`

- [x] **Step 1: Add `delivery_framework` field to the response model**

In `src/codemie/rest_api/models/cli_analytics.py`, add one field to `LocalAnalyticsSessionRow` (after `bloat_pct` on line ~182):

```python
delivery_framework: str | None = Field(None, description="Detected delivery framework")
```

`LocalAnalyticsSessionDetail` subclasses `LocalAnalyticsSessionRow` so it inherits the field automatically — no change there.

- [x] **Step 2: Write the failing handler tests**

Create `tests/codemie/service/analytics/handlers/test_cli_analytics_handler_framework.py`:

```python
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from codemie.repository.cli_analytics_repository import LocalAnalyticsFilter
from codemie.service.analytics.handlers.cli_analytics_handler import LocalAnalyticsHandler

_START = datetime(2026, 1, 1, tzinfo=timezone.utc)
_END = datetime(2026, 1, 2, tzinfo=timezone.utc)

_COST_ROW = {
    "session_id": "ses-1",
    "developer_name": "dev@x.com",
    "repository": "myrepo",
    "branch": "main",
    "first_prompt": "hello",
    "started_at": _START,
    "last_event_at": _END,
    "model_name": "claude-sonnet-4-6",
    "input_tokens": 100,
    "output_tokens": 50,
    "cache_read_tokens": 0,
    "cache_creation_tokens": 0,
    "cost_usd": 0.001,
}


def _make_repo(skill_names: list[str] | None = None) -> MagicMock:
    repo = MagicMock()
    repo.get_session_cost_facts = AsyncMock(return_value=[_COST_ROW])
    repo.get_turns_by_session = AsyncMock(return_value=[])
    repo.get_tool_success_by_session = AsyncMock(return_value=[])
    repo.get_lines_by_session = AsyncMock(return_value=[])
    repo.get_model_breakdown = AsyncMock(return_value=[])
    repo.get_skill_names_by_session = AsyncMock(
        return_value=(
            [{"session_id": "ses-1", "skill_names": skill_names}]
            if skill_names is not None
            else []
        )
    )
    return repo


def _make_filter() -> LocalAnalyticsFilter:
    return LocalAnalyticsFilter(start_dt=_START, end_dt=_END)


@pytest.mark.asyncio
async def test_delivery_framework_codemie_ai_factory():
    repo = _make_repo(["sdlc-factory:tech-analyst", "superpowers:brainstorming"])
    handler = LocalAnalyticsHandler(repo)
    data, _, _ = await handler.get_sessions(_make_filter(), 0, 20, "start_time", None)
    assert data["sessions"][0]["delivery_framework"] == "CodeMie AI Factory"


@pytest.mark.asyncio
async def test_delivery_framework_superpowers():
    repo = _make_repo(["superpowers:brainstorming"])
    handler = LocalAnalyticsHandler(repo)
    data, _, _ = await handler.get_sessions(_make_filter(), 0, 20, "start_time", None)
    assert data["sessions"][0]["delivery_framework"] == "Superpowers"


@pytest.mark.asyncio
async def test_delivery_framework_pure_chat_when_no_skills():
    repo = _make_repo([])
    handler = LocalAnalyticsHandler(repo)
    data, _, _ = await handler.get_sessions(_make_filter(), 0, 20, "start_time", None)
    assert data["sessions"][0]["delivery_framework"] == "Pure chat"


@pytest.mark.asyncio
async def test_delivery_framework_pure_chat_when_session_not_in_skill_rows():
    # skill_rows returns nothing for this session (e.g. >90 day TTL)
    repo = _make_repo(None)
    handler = LocalAnalyticsHandler(repo)
    data, _, _ = await handler.get_sessions(_make_filter(), 0, 20, "start_time", None)
    assert data["sessions"][0]["delivery_framework"] == "Pure chat"


@pytest.mark.asyncio
async def test_framework_filter_reduces_sessions_and_total():
    # Two sessions: one with sdlc-factory skill, one without
    cost_row_2 = {**_COST_ROW, "session_id": "ses-2"}
    repo = MagicMock()
    repo.get_session_cost_facts = AsyncMock(return_value=[_COST_ROW, cost_row_2])
    repo.get_turns_by_session = AsyncMock(return_value=[])
    repo.get_tool_success_by_session = AsyncMock(return_value=[])
    repo.get_lines_by_session = AsyncMock(return_value=[])
    repo.get_model_breakdown = AsyncMock(return_value=[])
    repo.get_skill_names_by_session = AsyncMock(return_value=[
        {"session_id": "ses-1", "skill_names": ["sdlc-factory:tech-analyst"]},
        # ses-2 intentionally absent — no skills → "Pure chat"
    ])
    handler = LocalAnalyticsHandler(repo)
    data, _, _ = await handler.get_sessions(
        _make_filter(), 0, 20, "start_time", None, framework="CodeMie AI Factory"
    )
    assert data["total"] == 1
    assert len(data["sessions"]) == 1
    assert data["sessions"][0]["delivery_framework"] == "CodeMie AI Factory"
```

- [x] **Step 3: Run tests to verify they fail**

```
pytest tests/codemie/service/analytics/handlers/test_cli_analytics_handler_framework.py -v
```

Expected: `TypeError` — `get_sessions` does not accept `framework` param yet, and `delivery_framework` key missing from session dict.

- [x] **Step 4: Extend `get_sessions` in the handler**

In `src/codemie/service/analytics/handlers/cli_analytics_handler.py`:

Add the import at the top of the file (after the existing imports):
```python
from codemie.service.analytics.delivery_framework import classify_delivery_framework
```

Change the `get_sessions` signature to accept `framework`:
```python
async def get_sessions(
    self,
    f: LocalAnalyticsFilter,
    page: int,
    per_page: int,
    sort_by: str,
    search: str | None,
    framework: str | None = None,
) -> tuple[dict, list[str], str | None]:
```

After the existing 5-way `asyncio.gather` (line ~441), add:
```python
session_ids = [_s(r.get("session_id")) for r in cost_facts]
skill_rows = await self._repo.get_skill_names_by_session(session_ids)
skills_by_session = {_s(r["session_id"]): r["skill_names"] for r in skill_rows}
```

Inside the `sessions.append({...})` block, add the new field after `"bloat_pct"`:
```python
"delivery_framework": classify_delivery_framework(skills_by_session.get(sid, [])),
```

After the existing `search` filter block (around line ~490), add the framework filter:
```python
if framework:
    sessions = [s for s in sessions if s.get("delivery_framework") == framework]
```

- [x] **Step 5: Add `framework` query param to the router**

In `src/codemie/rest_api/routers/cli_analytics.py`, update the `get_sessions` endpoint:

```python
@router.get("/sessions", response_model=LocalAnalyticsSessionsResponse, summary="Local analytics session list")
@handle_errors("local analytics sessions")
async def get_sessions(
    user: User = Depends(authenticate),
    filters: FilterParams = Depends(FilterParams),
    page: int | None = Query(None, ge=0),
    per_page: int | None = Query(None, ge=1, le=MAX_PER_PAGE),
    sort_by: Literal["start_time", "cost_usd", "ctx_per_call"] = Query("start_time"),
    search: str | None = Query(None, description="Filter by prompt, repository or branch"),
    framework: str | None = Query(None, description="Filter by delivery framework"),
) -> JSONResponse:
    _ensure_enabled()
    start_ns = time.monotonic_ns()
    f = await filters.resolve(user)
    resolved_page = page if page is not None else DEFAULT_PAGE
    resolved_per_page = per_page if per_page is not None else DEFAULT_PER_PAGE
    data, unpriced, data_as_of = await _handler.get_sessions(
        f, resolved_page, resolved_per_page, sort_by, search, framework
    )
    return _respond(
        {"data": data, "metadata": _metadata(start_ns, data_as_of, unpriced, f.end_dt)},
        LocalAnalyticsSessionsResponse,
    )
```

- [x] **Step 6: Run tests to verify they pass**

```
pytest tests/codemie/service/analytics/handlers/test_cli_analytics_handler_framework.py -v
```

Expected: all 5 tests PASS.

---

### Task 4: Session detail — handler extension

**Files:**
- Modify: `src/codemie/service/analytics/handlers/cli_analytics_handler.py` (extend `get_session_detail`)
- Modify: `tests/codemie/service/analytics/handlers/test_cli_analytics_handler_framework.py` (add detail tests)

**Interfaces:**
- Consumes: `classify_delivery_framework` from Task 1 (already imported in Task 3)
- Consumes: `LocalAnalyticsRepository.get_skill_names_by_session([session_id])` from Task 2

- [x] **Step 1: Write the failing tests**

Append to `tests/codemie/service/analytics/handlers/test_cli_analytics_handler_framework.py`:

```python
def _make_detail_repo(skill_names: list[str] | None = None) -> MagicMock:
    repo = MagicMock()
    repo.get_session_detail_meta = AsyncMock(return_value=[{
        "session_id": "ses-1",
        "developer_name": "dev@x.com",
        "repository": "myrepo",
        "branch": "main",
        "first_prompt": "hello",
        "started_at": _START,
        "duration_ms": 60000,
    }])
    repo.get_session_detail_cost = AsyncMock(return_value=[{
        "model_name": "claude-sonnet-4-6",
        "input_tokens": 100,
        "output_tokens": 50,
        "cache_read_tokens": 0,
        "cache_creation_tokens": 0,
        "cost_usd": 0.001,
    }])
    repo.get_session_detail_scalars = AsyncMock(return_value=[{
        "turns": 2,
        "tool_call_count": 3,
        "active_ms": 5000,
        "agent_count": 0,
        "skill_count": 1,
        "lines_added": 10,
        "lines_removed": 2,
    }])
    repo.get_session_detail_tools = AsyncMock(return_value=[])
    repo.get_session_detail_events = AsyncMock(return_value=[])
    repo.get_session_detail_dispatches = AsyncMock(return_value=[])
    repo.get_skill_names_by_session = AsyncMock(
        return_value=(
            [{"session_id": "ses-1", "skill_names": skill_names}]
            if skill_names is not None
            else []
        )
    )
    return repo


@pytest.mark.asyncio
async def test_session_detail_delivery_framework_codemie():
    repo = _make_detail_repo(["sdlc-factory:tech-analyst"])
    handler = LocalAnalyticsHandler(repo)
    detail, _ = await handler.get_session_detail("ses-1")
    assert detail["delivery_framework"] == "CodeMie AI Factory"
    # verify called with single-element list
    repo.get_skill_names_by_session.assert_called_once_with(["ses-1"])


@pytest.mark.asyncio
async def test_session_detail_delivery_framework_pure_chat():
    repo = _make_detail_repo(None)  # no skill rows returned
    handler = LocalAnalyticsHandler(repo)
    detail, _ = await handler.get_session_detail("ses-1")
    assert detail["delivery_framework"] == "Pure chat"
```

- [x] **Step 2: Run tests to verify they fail**

```
pytest tests/codemie/service/analytics/handlers/test_cli_analytics_handler_framework.py::test_session_detail_delivery_framework_codemie tests/codemie/service/analytics/handlers/test_cli_analytics_handler_framework.py::test_session_detail_delivery_framework_pure_chat -v
```

Expected: `KeyError: 'delivery_framework'` — field not yet in `detail` dict.

- [x] **Step 3: Extend `get_session_detail` in the handler**

In `src/codemie/service/analytics/handlers/cli_analytics_handler.py`, update `get_session_detail`:

Change the `asyncio.gather` call (line ~511) to add a 7th entry:
```python
meta_rows, cost_rows, scalar_rows, tool_rows, event_rows, dispatch_rows, skill_rows = await asyncio.gather(
    self._repo.get_session_detail_meta(session_id),
    self._repo.get_session_detail_cost(session_id),
    self._repo.get_session_detail_scalars(session_id),
    self._repo.get_session_detail_tools(session_id),
    self._repo.get_session_detail_events(session_id),
    self._repo.get_session_detail_dispatches(session_id),
    self._repo.get_skill_names_by_session([session_id]),
)
```

After assembling the `detail` dict (after the `"dispatches": dispatches` line, around line ~594), add:
```python
skill_names = _first(skill_rows).get("skill_names") or []
detail["delivery_framework"] = classify_delivery_framework(skill_names)
```

- [x] **Step 4: Run all framework tests to verify they pass**

```
pytest tests/codemie/service/analytics/handlers/test_cli_analytics_handler_framework.py -v
```

Expected: all 7 tests PASS.

- [x] **Step 5: Run the full test suite**

```
pytest tests/codemie/service/analytics/ tests/codemie/repository/test_cli_analytics_skill_query.py -v
```

Expected: all tests PASS, no regressions.

---

### Task 5: Frameworks list endpoint

**Files:**
- Modify: `src/codemie/service/analytics/delivery_framework.py` (add `get_framework_labels`)
- Modify: `src/codemie/rest_api/models/cli_analytics.py` (add `LocalAnalyticsFrameworksResponse`)
- Modify: `src/codemie/rest_api/routers/cli_analytics.py` (add `GET /frameworks`)
- Modify: `tests/codemie/service/analytics/test_delivery_framework.py` (add `get_framework_labels` tests)
- Create: `tests/codemie/rest_api/routers/test_cli_analytics_frameworks.py`

**Interfaces:**
- Produces: `get_framework_labels() -> list[str]` in `delivery_framework.py`
- Produces: `LocalAnalyticsFrameworksResponse` in models
- Produces: `GET /v1/analytics/cli-analytics/frameworks` → `{"data": list[str]}`

- [ ] **Step 1: Write the failing tests**

Add to `tests/codemie/service/analytics/test_delivery_framework.py`:

```python
from codemie.service.analytics.delivery_framework import classify_delivery_framework, get_framework_labels


def test_get_framework_labels_order():
    labels = get_framework_labels()
    assert labels == ["CodeMie AI Factory", "Superpowers", "BMAD", "OpenSpec", "Spec Kit", "Pure chat"]


def test_get_framework_labels_pure_chat_last():
    labels = get_framework_labels()
    assert labels[-1] == "Pure chat"
```

Create `tests/codemie/rest_api/routers/test_cli_analytics_frameworks.py`:

```python
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from codemie.rest_api.routers.cli_analytics import router


def test_frameworks_returns_ordered_list():
    from codemie.service.analytics.delivery_framework import get_framework_labels
    labels = get_framework_labels()
    assert "CodeMie AI Factory" in labels
    assert "Pure chat" in labels
    assert labels.index("CodeMie AI Factory") < labels.index("Pure chat")
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/codemie/service/analytics/test_delivery_framework.py::test_get_framework_labels_order tests/codemie/service/analytics/test_delivery_framework.py::test_get_framework_labels_pure_chat_last -v
```

Expected: `ImportError` — `get_framework_labels` does not exist yet.

- [ ] **Step 3: Implement the three changes**

In `src/codemie/service/analytics/delivery_framework.py`, add after `classify_delivery_framework`:

```python
def get_framework_labels() -> list[str]:
    """Return the ordered list of framework labels for use as filter options."""
    return [label for _, label in _RULES] + ["Pure chat"]
```

In `src/codemie/rest_api/models/cli_analytics.py`, add after `LocalAnalyticsSessionsData`:

```python
class LocalAnalyticsFrameworksResponse(BaseModel):
    data: list[str]
```

In `src/codemie/rest_api/routers/cli_analytics.py`:

1. Add to imports: `from codemie.service.analytics.delivery_framework import classify_delivery_framework, get_framework_labels`
2. Add to model imports: `LocalAnalyticsFrameworksResponse`
3. Add the endpoint before `GET /sessions`:

```python
@router.get("/frameworks", response_model=LocalAnalyticsFrameworksResponse, summary="Available delivery frameworks")
@handle_errors("local analytics frameworks")
async def get_frameworks(user: User = Depends(authenticate)) -> JSONResponse:
    _ensure_enabled()
    return _respond({"data": get_framework_labels()}, LocalAnalyticsFrameworksResponse)
```

- [ ] **Step 4: Run tests to verify they pass**

```
pytest tests/codemie/service/analytics/test_delivery_framework.py tests/codemie/rest_api/routers/test_cli_analytics_frameworks.py -v
```

Expected: all tests PASS.

- [ ] **Step 5: Run the full analytics test suite**

```
pytest tests/codemie/service/analytics/ tests/codemie/repository/test_cli_analytics_skill_query.py -v
```

Expected: all tests PASS, no regressions.
