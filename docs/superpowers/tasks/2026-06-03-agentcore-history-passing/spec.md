# AgentCore History Passing Support

## Overview

AgentCore runtime invocations currently send only the current user message. This spec covers adding optional conversation history injection into the request payload, configurable via the existing `configuration_json` structure stored on the assistant.

## Goals

- Allow operators to configure where in the request JSON the history array is placed.
- Allow operators to configure the per-turn shape (role field path, message field path, role labels).
- Non-breaking: `AgentcoreHistoryConfig` is optional in `AgentcoreRequestConfig`; existing configurations work unchanged.
- When no history config is present, no history is sent — even if a history list is available at the call site.

## Out of Scope

- Response parsing is not affected.
- History retrieval from storage is not affected (history is passed in from the call site).
- Streaming vs. non-streaming behaviour is unaffected.

---

## Config Model Changes

### New model: `AgentcoreHistoryConfig`

Added to `agentcore_config.py`. All fields are **required** (no defaults except the role/path convenience defaults — those represent the most common convention, but must be explicitly overridden if the target runtime uses a different shape):

```python
class AgentcoreHistoryConfig(BaseModel):
    history_path: str              # REQUIRED — dot-notation path where the history array is placed, e.g. "messages"
    role_path: str = "role"        # field within each turn object for the role label
    message_path: str = "content"  # field within each turn object for the message text
    user_role: str = "user"        # role label emitted for ChatRole.USER turns
    assistant_role: str = "assistant"  # role label emitted for all non-user turns
```

`history_path` is the only truly required field (no default). `role_path`, `message_path`, `user_role`, and `assistant_role` carry sensible defaults but are overridable — they are not optional in the sense that they always have a value.

### Updated model: `AgentcoreRequestConfig`

```python
class AgentcoreRequestConfig(BaseModel):
    message_path: str = "message"
    history: Optional[AgentcoreHistoryConfig] = None  # NEW — optional
```

---

## Request Builder Changes

`AgentcoreRequestBuilder.build()` gains an optional `history` parameter:

```python
def build(
    self,
    config: AgentcoreRequestConfig,
    user_query: str,
    history: Optional[List] = None,
) -> bytes
```

**Behaviour:**

1. Write `user_query` at `config.message_path` as before.
2. If `config.history` is `None`: skip history injection entirely — history is never sent without a history config, regardless of what is passed in the `history` argument.
3. If `config.history` is set but `history` is empty/None: skip history injection (no key written).
4. Otherwise: for each `ChatMessage` in `history`, build a turn dict using `set_json_path`:
   - `set_json_path(turn, config.history.role_path, role_label)` where `role_label` is `config.history.user_role` for `ChatRole.USER` and `config.history.assistant_role` for everything else.
   - `set_json_path(turn, config.history.message_path, message.message or "")`
5. Write the resulting list at `config.history.history_path` via `set_json_path(payload, config.history.history_path, turns)`.

---

## Runtime Service Changes

### `invoke_agentcore_runtime`

Gains `history: Optional[List] = None` (default preserves current callers):

```python
@staticmethod
def invoke_agentcore_runtime(
    assistant: Assistant,
    input_text: str,
    conversation_id: str,
    history: Optional[List] = None,
) -> InvokeAgentCoreRuntimeResponse:
```

### `_build_agentcore_request`

Gains `history: Optional[List] = None` and passes it to the builder:

```python
@staticmethod
def _build_agentcore_request(
    config: Optional[AgentcoreConfig],
    input_text: str,
    history: Optional[List] = None,
) -> tuple[bytes, str]:
```

### `BedrockOrchestratorService.invoke_bedrock_assistant`

Passes the existing `chat_history` argument to `invoke_agentcore_runtime`:

```python
return BedrockAgentCoreRuntimeService.invoke_agentcore_runtime(
    assistant=assistant,
    input_text=input_text,
    conversation_id=conversation_id,
    history=chat_history,
)
```

---

## Example

### Configuration JSON

```json
{
  "request": {
    "message_path": "query",
    "history": {
      "history_path": "messages",
      "role_path": "role",
      "message_path": "content",
      "user_role": "user",
      "assistant_role": "assistant"
    }
  },
  "response": {
    "streaming": false,
    "body": {"text_path": "output"}
  }
}
```

### Resulting request payload

```json
{
  "query": "What is the weather today?",
  "messages": [
    {"role": "user",      "content": "Hello"},
    {"role": "assistant", "content": "Hi there! How can I help?"}
  ]
}
```

---

## Testing

### `test_agentcore_config.py`

- `AgentcoreHistoryConfig` validates required `history_path`; role fields default correctly.
- `AgentcoreRequestConfig` accepts and rejects history sub-config; defaults to `None`.
- `AgentcoreConfig.parse_json` round-trips a config with history.

### `test_agentcore_request_builder.py`

- History injected at the configured `history_path`.
- Empty history list is skipped (no key written to payload).
- `None` history is skipped.
- Role mapping: `ChatRole.USER → user_role`, `ChatRole.ASSISTANT → assistant_role`.
- Custom `role_path` / `message_path` produce correctly nested turn objects.
- No `history` config → payload unchanged (backward compat).

---

## Acceptance Criteria

1. An assistant with no `history` field in `configuration_json` works identically to today — no history key is ever written to the payload.
2. An assistant with `history` config and non-empty history receives a correctly shaped turns array at `history_path`.
3. An assistant with `history` config but empty/None history sends no history key in the payload.
4. `history_path` is required inside `AgentcoreHistoryConfig`; omitting it raises a validation error.
5. Role labels, field paths, and convenience defaults are all configurable.
6. All existing tests continue to pass.
7. New tests cover the above scenarios.
