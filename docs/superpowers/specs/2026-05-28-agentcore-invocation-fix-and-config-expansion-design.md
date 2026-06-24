# Design — AgentCore Invocation Fix & Configuration Expansion

**Date**: 2026-05-28  
**Branch**: EPMCDME-12240_agentcore  
**Status**: Approved

---

## Problem

1. **Invocation is broken** — `invoke_agentcore_runtime()` guards on `runtime_endpoint_arn` but calls AWS with `runtime_arn`. The same inconsistency exists in `is_bedrock_assistant()`. If `runtime_arn` is absent the guard passes but the call fails silently.

2. **`accept` header is hardcoded** — `text/event-stream` is always sent regardless of what the endpoint supports, making the `application/json` parse branch unreachable.

3. **Response extraction is hardcoded** — `_parse_json_response` guesses `.get("response")` with no documentation backing. `_parse_streaming_response` treats every SSE data line as raw text with no JSON extraction.

4. **No thought support** — the AgentCore path bypasses the `StreamedGenerationResult` / `Thought` pipeline entirely. Thoughts are never emitted.

5. **`__QUERY_PLACEHOLDER__` is fragile** — a string sentinel with bespoke validation logic; not needed once the request path is declared explicitly.

---

## Solution

Replace the single `invocation_json` string with a structured `configuration_json` object that declares how to build the request and how to parse the response. Fix the ARN guard. Wire thought emission into the existing `Thought` pipeline.

---

## configuration_json Schema

All fields except `streaming`, `body`/`chunk` (conditional on `streaming`), and `text_path` within those blocks are optional.

```json
{
  "request": {
    "message_path": "input"
  },
  "response": {
    "streaming": false,
    "body": {
      "text_path": "output",
      "reasoning": {
        "text_path": "thinking",
        "active_path": "in_progress",
        "name_path": null,
        "args_path": null
      }
    },
    "chunk": {
      "text_path": "delta",
      "reasoning": {
        "text_path": "thinking",
        "active_path": "in_progress",
        "name_path": null,
        "args_path": null
      }
    }
  }
}
```

### Validation rules

| Condition | Rule |
|---|---|
| `response.streaming == false` | `body` required; `body.text_path` required |
| `response.streaming == true` | `chunk` required; `chunk.text_path` required |
| `body.reasoning` present | `text_path` and `active_path` required inside it |
| `chunk.reasoning` present | `text_path` and `active_path` required inside it |
| `name_path`, `args_path` | always optional |

### Backward compatibility

Existing imports stored as a flat `invocation_json` string (e.g. `{"message": "__QUERY_PLACEHOLDER__"}`) remain readable. At invocation time, if `configuration_json` does not parse as the new schema, fall back to the old `__QUERY_PLACEHOLDER__` replacement logic. New imports always use the new schema.

---

## Parser Architecture

Logic is split across three focused modules under `src/codemie/service/aws_bedrock/agentcore/`:

### `agentcore_config.py` — schema models only

```python
class AgentcoreReasoningConfig(BaseModel): ...
class AgentcoreBodyConfig(BaseModel): ...      # shared shape for body and chunk
class AgentcoreResponseConfig(BaseModel): ...
class AgentcoreRequestConfig(BaseModel): ...
class AgentcoreConfig(BaseModel): ...          # top-level; validates body/chunk requirement per streaming flag

def parse_configuration_json(raw: str | None) -> AgentcoreConfig | None: ...
# detects old __QUERY_PLACEHOLDER__ format for backward compat
```

### `agentcore_request_builder.py` — request construction only

```python
def resolve_path(data: dict, path: str) -> Any: ...
def set_path(data: dict, path: str, value: Any) -> None: ...

class AgentcoreRequestBuilder:
    def build(self, config: AgentcoreRequestConfig, user_query: str) -> bytes: ...
```

### `agentcore_response_parser.py` — response parsing only

```python
class AgentcoreResponseParser:
    def parse_json(self, body: bytes, config: AgentcoreResponseConfig) -> tuple[str, list[Thought]]: ...
    def parse_streaming(self, stream, config: AgentcoreResponseConfig) -> tuple[str, list[Thought]]: ...

    # shared — called from both parse_json and the per-chunk loop in parse_streaming
    def _extract_reasoning(self, data: dict, config: AgentcoreBodyConfig) -> Thought | None: ...
```

`body` and `chunk` both use `AgentcoreBodyConfig` so `_extract_reasoning` is written once. Both parsers return `(answer_text, list[Thought])` — the service layer emits thoughts without knowing SSE or JSON internals. The streaming thought state machine lives entirely inside `parse_streaming`.

No parsing or building logic appears anywhere else — not in the service, not in the router.

---

## Path Resolution

All `*_path` values use dot-notation. An empty segment or absent key returns `None`.

```
"result.answer"   →  data["result"]["answer"]
"choices.0.text"  →  data["choices"][0]["text"]
"output"          →  data["output"]
```

Implemented as a single `resolve_path(data: dict, path: str) -> Any` utility.

---

## Request Building

```python
payload = {}
set_path(payload, config.request.message_path, user_query)
# e.g. message_path="input.query" → {"input": {"query": "<user query>"}}
```

Replaces `_prepare_invocation_payload` and the `__QUERY_PLACEHOLDER__` mechanism entirely.

---

## Response Parsing

### Non-streaming (`streaming: false`)

1. Send `accept: application/json`.
2. Read full body, parse as JSON.
3. Extract `resolve_path(body, response.body.text_path)` → answer text.
4. If `response.body.reasoning` configured: extract thought fields and emit one `Thought(in_progress=False)`.

### Streaming (`streaming: true`)

1. Send `accept: text/event-stream`.
2. Iterate SSE `data:` lines; skip empty lines.
3. Per line:
   - If `chunk.text_path` set: parse line as JSON, extract text via `resolve_path`.
   - Otherwise: use raw line as text.
4. Thought state machine (only when `chunk.reasoning` configured):
   - Extract `reasoning.text_path`, `reasoning.active_path`, `reasoning.name_path`, `reasoning.args_path` from the chunk JSON.
   - If `active_path` resolves to `True`: emit `Thought(in_progress=True, message=..., author_name=..., input_text=...)`.
   - If `active_path` resolves to `False`: emit `Thought(in_progress=False, ...)` → thought closes.
   - Thought closes implicitly when stream ends while still active.
   - A new thought opens when `active_path=True` arrives after a previous thought closed.
5. Stream ends when connection closes.

---

## Thought Integration

`invoke_agentcore_runtime` returns an extended `InvokeAgentCoreRuntimeResponse`:

```python
class InvokeAgentCoreRuntimeResponse(TypedDict):
    output: str
    thoughts: list[dict]   # new — list of Thought.model_dump() dicts
    time_elapsed: float
```

In `AIToolsAgent._agent_streaming()`, before `process_output`:

```python
for thought_dict in response.get("thoughts", []):
    self.thread_generator.send(
        StreamedGenerationResult(thought=Thought(**thought_dict)).model_dump_json()
    )
```

For the non-streaming `_invoke_agent` path, thoughts are not emitted — the sync path has no active `ThreadedGenerator` to send frames to. This matches the existing pattern where sync responses return a plain `GenerationResult` without thought streaming.

---

## ARN Bug Fix

**`is_bedrock_assistant()`** (`bedrock_orchestration_service.py:93`):
```python
# Before
assistant.bedrock_agentcore_runtime.runtime_endpoint_arn
# After
assistant.bedrock_agentcore_runtime.runtime_arn
```

**`invoke_agentcore_runtime()` guard** (`bedrock_agentcore_runtime_service.py:363`):
```python
# Before
not assistant.bedrock_agentcore_runtime.runtime_endpoint_arn
# After
not assistant.bedrock_agentcore_runtime.runtime_arn
```

Both now guard on the same field that the actual boto3 call uses.

---

## Model Changes

### `BedrockAgentcoreRuntimeData` (`assistant.py:252`)

- Remove `invocation_json: str`
- Add `configuration_json: Optional[str] = None` — stores the new structured JSON string
- Keep `runtime_arn`, `runtime_endpoint_arn`, and all other existing fields unchanged

### `ImportAgentcoreRuntime` (`vendor.py:40`)

- Remove `configuration_json: str` (required)
- Add `configuration_json: Optional[str] = None` — new structured format; omitting falls back to legacy

### `AgentcoreEndpointEntity` / `AgentcoreEndpointDetailEntity`

- `configurationJson` field now returns the new structured JSON string

---

## Affected Files

| File | Change |
|---|---|
| `src/codemie/rest_api/models/assistant.py` | `BedrockAgentcoreRuntimeData`: swap `invocation_json` → `configuration_json` |
| `src/codemie/rest_api/models/vendor.py` | `ImportAgentcoreRuntime`: make `configuration_json` optional |
| `src/codemie/service/aws_bedrock/bedrock_orchestration_service.py` | Fix ARN field in `is_bedrock_assistant()` |
| `src/codemie/service/aws_bedrock/bedrock_agentcore_runtime_service.py` | Fix ARN guard; extend `InvokeAgentCoreRuntimeResponse`; new streaming/JSON parsers |
| `src/codemie/service/aws_bedrock/agentcore/bedrock_agentcore_endpoint_service.py` | Replace `_prepare_invocation_payload` + `_validate_invocation_json` + `_contains_placeholder`; new `build_request_payload` + `parse_configuration_json` |
| `src/codemie/agents/assistant_agent.py` | Emit thought frames from AgentCore response in `_agent_streaming` and `_invoke_agent` |
| `tests/codemie/service/aws_bedrock/test_bedrock_agentcore_runtime_service.py` | Add parse tests for streaming and JSON paths |

---

## Out of Scope

- No Alembic migration — `configuration_json` is stored as a JSONB string column that already exists; new schema is backward-compatible with old string values.
- No changes to the Bedrock Agent (`BEDROCK_AGENT`) path.
- No frontend changes beyond what the existing `configurationJson` field already exposes.
