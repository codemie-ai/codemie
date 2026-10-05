# Request Hedging

## Quick Summary

Request hedging is a latency-reduction pattern that races a fast-path tool against the full AI agent pipeline. If the fast path answers within a configurable timeout, the agent is cancelled and the fast-path result is returned immediately. If the fast path misses, the agent answer is already in-flight — zero added latency.

**Category**: Development / Performance | **Complexity**: Medium | **Prerequisites**: An existing assistant, a DSP provider or a custom `CodeMieHedgeTool` implementation

---

## How It Works

```
User Request
    │
    ├──► Fast-path thread  ──► (cache / retrieval / DSP tool)
    │                                   │
    └──► Agent thread      ──► (LangGraph / LangChain pipeline)
                                        │
    ── timeout_ms ──────────────────────┤
                                        │
              Fast-path non-empty? ─────┤
                  YES → cancel agent, return fast-path result
                  NO  → stream agent result (already running)
```

- Both threads start at the **same instant** — no sequential overhead.
- The fast path is given `timeout_ms` milliseconds (default 200 ms) to produce a non-empty result.
- The agent response is always available as fallback with no additional wait.

---

## Configuration

Add `hedging_config` to an assistant on create or update.

### Schema

```python
class HedgingConfig(BaseModel):
    tool: HedgingToolDetails | None = None           # internal CodeMieHedgeTool
    provider_tool: HedgingProviderToolDetails | None  # external DSP provider tool
    timeout_ms: int = 200                             # fast-path deadline (ms, must be > 0)
    input_mapping: dict[str, str] = {}               # Jinja2 templates → tool parameters
    output_field: str | None = None                  # dot-notation path in provider response
```

**Constraint**: exactly one of `tool` or `provider_tool` must be set.

### Jinja2 Variables Available in `input_mapping`

| Variable | Value |
|---|---|
| `{{query}}` | User message text |
| `{{conversation_id}}` | Conversation UUID |
| `{{user.id}}` | User ID |
| `{{user.name}}` | User display name |
| `{{user.username}}` | Username |
| `{{user.email}}` | User email |
| `{{user.token}}` | Bearer token (**sensitive** — only use with trusted internal providers) |
| `{{headers.<name>}}` | Any HTTP request header |
| `{{metadata.<key>}}` | Any metadata field from the chat request |

---

## Option A: Internal Tool (`tool`)

Use a registered `CodeMieHedgeTool` as the fast path.

### Assistant Config Example

```python
{
    "name": "My Hedged Assistant",
    "hedging_config": {
        "tool": {"name": "example_hedge_tool"},
        "timeout_ms": 150,
        "input_mapping": {
            "query": "{{query}}",
            "metadata": "{\"user_id\": \"{{user.id}}\"}"
        }
    }
}
```

**`input_mapping` rules for internal tools**:
- `"query"` → mapped to `HedgeToolInput.query` (the search/retrieval string).
- All other keys → merged into `HedgeToolInput.metadata` dict.

### Creating a Custom Internal Tool

1. **Create a new file** under `src/codemie_tools/` in a `DiscoverableToolkit` package.
2. **Subclass `CodeMieHedgeTool`** and implement `execute()`.

```python
# src/codemie_tools/my_package/my_hedge_tool.py
from codemie_tools.base.codemie_hedge_tool import CodeMieHedgeTool, HedgeToolResult

class MyHedgeTool(CodeMieHedgeTool):
    name = "my_hedge_tool"
    description = "Fast lookup via my internal cache."

    async def execute(self, query: str, metadata: dict) -> HedgeToolResult:
        result = await my_cache.get(query)
        if result is None:
            return HedgeToolResult(empty=True)
        return HedgeToolResult(empty=False, data=result)
```

3. **Register in a toolkit** with `is_hedgeable=True` on the tool class.

```python
# src/codemie_tools/my_package/toolkit.py
from codemie_tools.base.models import Tool, ToolKit
from codemie_tools.base.toolkit_provider import DiscoverableToolkit
from .my_hedge_tool import MyHedgeTool
from .tools_vars import MY_HEDGE_TOOL

class MyHedgingToolkit(DiscoverableToolkit):
    is_hedging_only: ClassVar[bool] = True   # exclude from normal agent tools list

    definition = ToolKit(
        name="My Hedging Toolkit",
        tools=[Tool(metadata=MY_HEDGE_TOOL, tool_class=MyHedgeTool)]
    )
```

4. Auto-discovery picks up the toolkit on next startup — no manual registration needed.

**`HedgeToolResult` contract**:
- `empty=True` → fast path has no answer; agent continues.
- `empty=False, data=<str|dict|Any>` → fast path wins; `data` is returned to the user.

---

## Option B: Provider / DSP Tool (`provider_tool`)

Use an externally registered DSP provider tool as the fast path.

### Assistant Config Example

```python
{
    "name": "My Hedged Assistant",
    "hedging_config": {
        "provider_tool": {
            "provider_name": "my-dsp-provider",
            "toolkit_name": "SearchToolkit",
            "tool_name": "semantic_search",
            "result_condition": "results != [] and results != null"
        },
        "timeout_ms": 300,
        "input_mapping": {
            "query": "{{query}}",
            "user_id": "{{user.id}}",
            "project_id": "{{metadata.project_id}}"
        },
        "output_field": "results.0.text"
    }
}
```

### `provider_tool` Fields

| Field | Required | Description |
|---|---|---|
| `provider_name` | Yes | Name of the DSP Provider in the database |
| `toolkit_name` | Yes | Toolkit on that provider |
| `tool_name` | Yes | Tool within the toolkit |
| `result_condition` | No | Python boolean expression to accept/reject the result (see below) |

### `result_condition` — Accepting Provider Results

A Python expression evaluated against the raw result dict. Dict keys are available as top-level variables. JSON-style `false`, `true`, and `null` are available as aliases.

```python
# Accept if non-null and non-empty list
"results != null and results != []"

# Accept if explicit flag says not empty
"empty == false"

# Accept if confidence threshold met
"score >= 0.8"

# Compound condition
"status == 'ok' and data != null"
```

- Returns `empty=True` (falls back to agent) if the expression evaluates to `False` or raises an error.
- If `result_condition` is `None`, any non-null Completed result is accepted.

**Safe expression restrictions** (`safe_eval`): supports comparison operators, `and/or/not`, `==`, `!=`, `in`, standard builtins (`len`, `min`, `max`, `sum`, `abs`, `any`, `all`, `isinstance`). Blocks `__import__`, `eval`, `exec`, `compile`, `open`, all dunders.

### `output_field` — Extracting a Nested Value

Dot-notation path into the provider result. Integer segments index into lists.

```python
"data.answer"          # result["data"]["answer"]
"results.0.text"       # result["results"][0]["text"]
"payload.items.2.id"   # result["payload"]["items"][2]["id"]
```

If `output_field` is `None`, the entire result dict is used as the response.

---

## Discovering Available Hedging Tools

```http
GET /assistants/hedgeable_tools
Authorization: Bearer <token>
```

Returns all registered toolkits containing `is_hedgeable=True` tools. Use this endpoint to browse available fast-path tool names when configuring an assistant.

---

## Tuning `timeout_ms`

| Scenario | Recommended `timeout_ms` |
|---|---|
| In-process memory cache | 10–50 ms |
| Local DB lookup | 50–150 ms |
| Internal HTTP microservice | 100–300 ms |
| External API (same datacenter) | 200–500 ms |
| External API (cross-region) | 500–1000 ms |

**Principle**: set `timeout_ms` to the P95 latency of the fast path. If it often exceeds this, consider whether hedging provides value.

---

## Handler Selection (Automatic)

The handler factory in `assistant_handlers.py` selects `HedgedAssistantHandler` automatically when `assistant.hedging_config is not None`. No other code changes are needed.

```python
# assistant_handlers.py — factory logic (simplified)
def get_request_handler(assistant, user, request_uuid):
    if assistant.type == AssistantType.A2A:
        return A2AAssistantHandler(...)
    if assistant.hedging_config is not None:
        return HedgedAssistantHandler(...)   # ← selected when hedging_config set
    return StandardAssistantHandler(...)
```

---

## Streaming Response Format

When the fast path wins, the response contains three chunks in `application/x-ndjson`:

```jsonl
{"type": "thought", "tool_name": "<tool_display_name>", "in_progress": true}
{"type": "thought", "tool_name": "<tool_display_name>", "in_progress": false, "message": "<fast_path_data>"}
{"type": "result", "generated": "<fast_path_data>", "last": true, "time_elapsed": 0.12}
```

When the agent wins, chunks are the normal agent streaming format (unchanged).

---

## Common Pitfalls

| Pitfall | Fix |
|---|---|
| `timeout_ms` too short → always falls back | Profile fast-path P95 latency; set timeout above it |
| `result_condition` always rejects → always falls back | Test expression manually with a sample result dict |
| `output_field` path wrong → `KeyError` logged, falls back | Verify path against actual provider response shape |
| `{{user.token}}` in `input_mapping` for external provider | Only safe for fully trusted internal services |
| Internal tool constructor requires args | All `CodeMieHedgeTool` subclasses must support no-arg construction; use `.env`-backed defaults |
| New hedge tool not found by discovery | Ensure toolkit subclasses `DiscoverableToolkit` and resides under `codemie_tools/` |

---

## Database Migration

The `hedging_config` column was added in migration `r7s8t9u0v1w2`:

```python
# alembic/versions/r7s8t9u0v1w2_add_hedging_config_to_assistants.py
op.add_column('assistants', Column('hedging_config', JSONB(), nullable=True))
```

Run `alembic upgrade head` to apply on a fresh environment.

---

## Related Guides

- `.ai-run/guides/agents/custom-tool-creation.md` — tool base classes and registration
- `.ai-run/guides/development/performance-patterns.md` — async I/O, concurrency patterns
- `.ai-run/guides/integration/external-services.md` — DSP provider setup
- `.ai-run/guides/api/rest-api-patterns.md` — assistant CRUD endpoint patterns
