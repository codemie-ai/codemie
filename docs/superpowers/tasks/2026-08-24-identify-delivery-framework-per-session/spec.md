# Spec: Delivery Framework Detection for CLI Analytics Sessions

**Task dir**: `docs/superpowers/tasks/2026-08-24-identify-delivery-framework-per-session/`
**Branch**: `feature/EPMCDME-13580-code-agent-analytics`
**Ticket**: EPMCDME-14243

---

## Goal

Add a `delivery_framework` field to CLI Analytics session responses so each session is labelled with the primary structured framework used (or "Pure chat" when none is detected). Also support optional filtering by framework on the sessions list endpoint.

UI changes are out of scope — this spec covers backend only.

---

## Context

The analytics plugin already records skill names on `tool-start` events. These land in `codemie_analytics.coding_agent_logs` as `skill_name` (a `LowCardinality(String)` materialized column). No ClickHouse schema changes are required.

Framework classification belongs in Python — the existing handler already merges all ClickHouse results in Python, and the classifier is just another merge step following the same pattern.

---

## Classifier Module

**New file**: `src/codemie/service/analytics/delivery_framework.py`

Pure Python, no I/O, no imports from the rest of the app.

```python
_RULES: list[tuple[str, str]] = [
    ("sdlc-factory:",  "CodeMie AI Factory"),
    ("superpowers:",   "Superpowers"),
    ("bmad-",          "BMAD"),
    ("opsx:",          "OpenSpec"),
    ("speckit.",       "Spec Kit"),
]

def classify_delivery_framework(skill_names: list[str]) -> str:
    for prefix, label in _RULES:
        if any(s.startswith(prefix) for s in skill_names):
            return label
    return "Pure chat"
```

Priority is positional in `_RULES` — first match wins. Adding a new framework is one line.

When additional signal types are needed (e.g. `agent_types`, file markers), introduce a `SessionSignals` dataclass carrying all inputs and change the function signature to accept it. Call sites update once; the classifier internals stay the same.

When a session carries both `sdlc-factory:*` and `superpowers:*` skills (expected in production — sdlc-factory internally invokes superpowers:*), `"CodeMie AI Factory"` wins because it appears first in `_RULES`.

Sessions with no matching skill names (including sessions older than the 90-day `coding_agent_logs` TTL) return `"Pure chat"`.

---

## Response Model

**File**: `src/codemie/rest_api/models/cli_analytics.py`

Add one field to `LocalAnalyticsSessionRow`:

```python
delivery_framework: str | None = Field(None, description="Detected delivery framework")
```

`LocalAnalyticsSessionDetail` inherits it automatically via subclassing. No other model changes.

The field is nullable and optional — fully backward-compatible with the existing `codemie-ui` TypeScript contract.

---

## Repository

**File**: `src/codemie/repository/cli_analytics_repository.py`

One new method querying `coding_agent_logs` (the high-coverage skill source). Works for both multi-session and single-session callers — pass `[session_id]` from the detail endpoint:

```python
async def get_skill_names_by_session(self, session_ids: list[str]) -> list[dict]:
    """Distinct skill names per session from coding_agent_logs (skill_activated events).

    Uses coding_agent_logs.skill_name on skill_activated events — the only source
    that records framework-specific skill names (e.g. superpowers:brainstorming,
    sdlc-factory:sdlc-standard). Covers both slash-command activations and
    in-session Skill tool invocations. The api_request event carries generic names
    (third-party, codemie-sdk) used for cost attribution only.
    coding_agent_traces.span_skill_name has ~0.4% session coverage and is not used.

    Accepts already-filtered session IDs from cost_facts — no date filter needed.
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

No CTE, no date filter, no `_session_scope` — filter correctness is inherited from `cost_facts` which already applied `LocalAnalyticsFilter`. Future filter additions to `LocalAnalyticsFilter` automatically propagate without touching this method.

> **Data source rationale:** `coding_agent_logs.skill_name` (materialized from `LogAttributes['skill.name']`) on `skill_activated` events is the only source that carries framework-specific skill names (`superpowers:brainstorming`, `sdlc-factory:sdlc-standard`, etc.). The `api_request` event also has `skill_name != ''` rows, but those contain generic identifiers (`third-party`, `codemie-sdk`) used for cost attribution — not for framework detection. `coding_agent_traces.span_skill_name` has ~0.4% session coverage and is only used by `get_invocations` for the `/tools` endpoint, where it is intentional and independent.

---

## Handler

**File**: `src/codemie/service/analytics/handlers/cli_analytics_handler.py`

### `get_sessions`

After the existing 5-way `asyncio.gather`, add two sequential lines then inject `delivery_framework` into every session dict:

```python
# existing 5-gather unchanged
cost_facts, turns_rows, success_rows, lines_rows, model_rows = await asyncio.gather(...)

# skill names — sequential, uses already-resolved session IDs
session_ids = [_s(r.get("session_id")) for r in cost_facts]
skill_rows = await self._repo.get_skill_names_by_session(session_ids)
skills_by_session = {_s(r["session_id"]): r["skill_names"] for r in skill_rows}
```

Inside the session-building loop:
```python
"delivery_framework": classify_delivery_framework(skills_by_session.get(sid, [])),
```

### `get_session_detail`

Add `get_skill_names_by_session` as a 7th entry in the existing `asyncio.gather`, passing a single-element list:

```python
meta_rows, cost_rows, scalar_rows, tool_rows, event_rows, dispatch_rows, skill_rows = await asyncio.gather(
    self._repo.get_session_detail_meta(session_id),
    self._repo.get_session_detail_cost(session_id),
    self._repo.get_session_detail_scalars(session_id),
    self._repo.get_session_detail_tools(session_id),
    self._repo.get_session_detail_events(session_id),
    self._repo.get_session_detail_dispatches(session_id),
    self._repo.get_skill_names_by_session([session_id]),   # new
)
```

After assembling `detail`:
```python
skill_names = _first(skill_rows).get("skill_names") or []
detail["delivery_framework"] = classify_delivery_framework(skill_names)
```

---

## Delivery Frameworks List Endpoint

**File**: `src/codemie/rest_api/routers/cli_analytics.py`

New endpoint that returns the ordered list of framework labels. The UI fetches this once on mount to populate the filter dropdown. No date filter, no pagination — data is static.

```
GET /v1/analytics/cli-analytics/frameworks
```

Response shape:
```json
{ "data": ["CodeMie AI Factory", "Superpowers", "BMAD", "OpenSpec", "Spec Kit", "Pure chat"] }
```

Order is positional in `_RULES` with `"Pure chat"` appended last, matching classifier priority. Auth-gated like all other endpoints; `_respond` applies `Cache-Control: private, max-age=300` automatically.

### `delivery_framework.py` — public accessor

Add one public function so the router does not access `_RULES` directly:

```python
def get_framework_labels() -> list[str]:
    return [label for _, label in _RULES] + ["Pure chat"]
```

### Response model

Add to `src/codemie/rest_api/models/cli_analytics.py`:

```python
class LocalAnalyticsFrameworksResponse(BaseModel):
    data: list[str]
```

### Router

```python
@router.get("/frameworks", response_model=LocalAnalyticsFrameworksResponse, summary="Available delivery frameworks")
@handle_errors("local analytics frameworks")
async def get_frameworks(user: User = Depends(authenticate)) -> JSONResponse:
    _ensure_enabled()
    return _respond({"data": get_framework_labels()}, LocalAnalyticsFrameworksResponse)
```

Place before `/sessions` in the router file.

---

## Router — Framework Filter

**File**: `src/codemie/rest_api/routers/cli_analytics.py`

Add an optional `framework` query param to `GET /sessions`:

```python
framework: str | None = Query(None, description="Filter by delivery framework")
```

Pass it through to the handler. In the handler, apply after framework classification and before sort (same pattern as `search`):

```python
if framework:
    sessions = [s for s in sessions if s.get("delivery_framework") == framework]
```

`total = len(sessions)` is computed after this filter, so pagination is correct.

Handler signature change:
```python
async def get_sessions(self, f, page, per_page, sort_by, search, framework=None)
```

---

## Endpoints Affected

| Endpoint | Change |
|---|---|
| `GET /v1/analytics/cli-analytics/frameworks` | **New** — returns ordered list of framework labels |
| `GET /v1/analytics/cli-analytics/sessions` | New `delivery_framework` field on each row; new optional `?framework=` query param |
| `GET /v1/analytics/cli-analytics/sessions/{trace_id}` | New `delivery_framework` field on detail |
| All other `/v1/analytics/cli-analytics/*` | No change |

---

## Files Changed

| File | Change |
|---|---|
| `src/codemie/service/analytics/delivery_framework.py` | New — classifier module; add `get_framework_labels()` |
| `src/codemie/rest_api/models/cli_analytics.py` | Add `delivery_framework` field to `LocalAnalyticsSessionRow`; add `LocalAnalyticsFrameworksResponse` |
| `src/codemie/repository/cli_analytics_repository.py` | Add `get_skill_names_by_session` |
| `src/codemie/service/analytics/handlers/cli_analytics_handler.py` | Extend `get_sessions` and `get_session_detail`; add `framework` param |
| `src/codemie/rest_api/routers/cli_analytics.py` | Add `framework` query param to `GET /sessions`; add `GET /frameworks` endpoint |

---

## Out of Scope

- UI changes (separate session)
- Git commits (user instruction)
- ClickHouse schema changes (none needed)
- New feature flags (additive field, existing gate covers it)
- YAML-backed rule configuration (hardcoded rules sufficient for MVP)
