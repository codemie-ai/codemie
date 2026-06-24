# AgentCore History Passing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend AgentCore request building to optionally inject conversation history into the runtime payload, driven entirely by an `AgentcoreHistoryConfig` nested in `AgentcoreRequestConfig`.

**Architecture:** Add `AgentcoreHistoryConfig` (new Pydantic model) and an optional `history` field to `AgentcoreRequestConfig`. `AgentcoreRequestBuilder.build()` gains an optional `history` parameter and uses `set_json_path` to place serialised turns at `config.history.history_path`. The `invoke_agentcore_runtime` / `_build_agentcore_request` methods thread the history down from the orchestrator call site.

**Tech Stack:** Python 3.11, Pydantic v2, pytest. No new dependencies.

---

## File Map

| Action | Path |
|---|---|
| Modify | `src/codemie/service/aws_bedrock/agentcore/agentcore_config.py` |
| Modify | `src/codemie/service/aws_bedrock/agentcore/agentcore_request_builder.py` |
| Modify | `src/codemie/service/aws_bedrock/bedrock_agentcore_runtime_service.py` |
| Modify | `src/codemie/service/aws_bedrock/bedrock_orchestration_service.py` |
| Test   | `tests/codemie/service/aws_bedrock/agentcore/test_agentcore_config.py` |
| Test   | `tests/codemie/service/aws_bedrock/agentcore/test_agentcore_request_builder.py` |

---

### Task 1: Add `AgentcoreHistoryConfig` and update `AgentcoreRequestConfig`

**Test-first: yes** — validation tests cover required field and defaults before the model is added.

**Files:**
- Modify: `src/codemie/service/aws_bedrock/agentcore/agentcore_config.py`
- Test: `tests/codemie/service/aws_bedrock/agentcore/test_agentcore_config.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/codemie/service/aws_bedrock/agentcore/test_agentcore_config.py`:

```python
from pydantic import ValidationError
from codemie.service.aws_bedrock.agentcore.agentcore_config import (
    AgentcoreHistoryConfig,
    AgentcoreRequestConfig,
    AgentcoreConfig,
)


def test_history_config_requires_history_path():
    with pytest.raises(ValidationError):
        AgentcoreHistoryConfig.model_validate({})  # history_path missing


def test_history_config_defaults():
    cfg = AgentcoreHistoryConfig(history_path="messages")
    assert cfg.role_path == "role"
    assert cfg.message_path == "content"
    assert cfg.user_role == "user"
    assert cfg.assistant_role == "assistant"


def test_history_config_custom_values():
    cfg = AgentcoreHistoryConfig(
        history_path="ctx.turns",
        role_path="speaker",
        message_path="text",
        user_role="human",
        assistant_role="bot",
    )
    assert cfg.history_path == "ctx.turns"
    assert cfg.role_path == "speaker"
    assert cfg.message_path == "text"
    assert cfg.user_role == "human"
    assert cfg.assistant_role == "bot"


def test_request_config_history_defaults_to_none():
    cfg = AgentcoreRequestConfig()
    assert cfg.history is None


def test_request_config_accepts_history():
    cfg = AgentcoreRequestConfig(
        message_path="query",
        history=AgentcoreHistoryConfig(history_path="messages"),
    )
    assert cfg.history.history_path == "messages"


def test_agentcore_config_parse_json_with_history():
    raw = json.dumps({
        "request": {
            "message_path": "query",
            "history": {"history_path": "messages"},
        },
        "response": {"streaming": False, "body": {"text_path": "output"}},
    })
    cfg = AgentcoreConfig.parse_json(raw)
    assert cfg.request.history.history_path == "messages"
    assert cfg.request.history.role_path == "role"
```

- [ ] **Step 2: Run to verify they fail**

```bash
poetry run pytest tests/codemie/service/aws_bedrock/agentcore/test_agentcore_config.py \
  -k "history" -v 2>&1 | tail -20
```

Expected: `ImportError: cannot import name 'AgentcoreHistoryConfig'`

- [ ] **Step 3: Add `AgentcoreHistoryConfig` and update `AgentcoreRequestConfig`**

In `src/codemie/service/aws_bedrock/agentcore/agentcore_config.py`, after the `AgentcoreReasoningConfig` class and before `AgentcoreOutputConfig`, insert:

```python
class AgentcoreHistoryConfig(BaseModel):
    """Controls how conversation history is serialised into the runtime request payload."""

    history_path: str
    role_path: str = "role"
    message_path: str = "content"
    user_role: str = "user"
    assistant_role: str = "assistant"
```

Then update `AgentcoreRequestConfig`:

```python
class AgentcoreRequestConfig(BaseModel):
    message_path: str = "message"
    history: Optional[AgentcoreHistoryConfig] = None
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
poetry run pytest tests/codemie/service/aws_bedrock/agentcore/test_agentcore_config.py \
  -k "history" -v 2>&1 | tail -20
```

Expected: all 6 new tests PASS, all existing tests still PASS.

- [ ] **Step 5: Run the full config test module**

```bash
poetry run pytest tests/codemie/service/aws_bedrock/agentcore/test_agentcore_config.py -v 2>&1 | tail -20
```

Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add src/codemie/service/aws_bedrock/agentcore/agentcore_config.py \
        tests/codemie/service/aws_bedrock/agentcore/test_agentcore_config.py
git commit -m "EPMCDME-12240: Add AgentcoreHistoryConfig and optional history field to AgentcoreRequestConfig"
```

---

### Task 2: Update `AgentcoreRequestBuilder.build()` to inject history

**Test-first: yes** — builder tests for history injection, empty-guard, and backward compat.

**Files:**
- Modify: `src/codemie/service/aws_bedrock/agentcore/agentcore_request_builder.py`
- Test: `tests/codemie/service/aws_bedrock/agentcore/test_agentcore_request_builder.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/codemie/service/aws_bedrock/agentcore/test_agentcore_request_builder.py`:

```python
import json
from codemie.core.constants import ChatRole
from codemie.core.models import ChatMessage
from codemie.service.aws_bedrock.agentcore.agentcore_config import (
    AgentcoreHistoryConfig,
    AgentcoreRequestConfig,
)
from codemie.service.aws_bedrock.agentcore.agentcore_request_builder import AgentcoreRequestBuilder


def _history_config(history_path="messages", **kwargs):
    return AgentcoreHistoryConfig(history_path=history_path, **kwargs)


def _chat_message(role: ChatRole, text: str) -> ChatMessage:
    return ChatMessage(role=role, message=text)


def test_build_with_history_injects_turns():
    builder = AgentcoreRequestBuilder()
    config = AgentcoreRequestConfig(
        message_path="query",
        history=_history_config("messages"),
    )
    history = [
        _chat_message(ChatRole.USER, "Hello"),
        _chat_message(ChatRole.ASSISTANT, "Hi there!"),
    ]
    result = json.loads(builder.build(config, "What next?", history))
    assert result["query"] == "What next?"
    assert result["messages"] == [
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi there!"},
    ]


def test_build_without_history_config_ignores_history():
    """No history config → history argument is silently ignored."""
    builder = AgentcoreRequestBuilder()
    config = AgentcoreRequestConfig(message_path="message")
    history = [_chat_message(ChatRole.USER, "Should not appear")]
    result = json.loads(builder.build(config, "hi", history))
    assert result == {"message": "hi"}


def test_build_with_history_config_and_none_history():
    builder = AgentcoreRequestBuilder()
    config = AgentcoreRequestConfig(
        message_path="query",
        history=_history_config("messages"),
    )
    result = json.loads(builder.build(config, "hi", None))
    assert "messages" not in result


def test_build_with_history_config_and_empty_history():
    builder = AgentcoreRequestBuilder()
    config = AgentcoreRequestConfig(
        message_path="query",
        history=_history_config("messages"),
    )
    result = json.loads(builder.build(config, "hi", []))
    assert "messages" not in result


def test_build_history_custom_role_labels():
    builder = AgentcoreRequestBuilder()
    config = AgentcoreRequestConfig(
        message_path="query",
        history=_history_config("turns", user_role="human", assistant_role="bot"),
    )
    history = [
        _chat_message(ChatRole.USER, "ping"),
        _chat_message(ChatRole.ASSISTANT, "pong"),
    ]
    result = json.loads(builder.build(config, "next", history))
    assert result["turns"][0]["role"] == "human"
    assert result["turns"][1]["role"] == "bot"


def test_build_history_custom_field_paths():
    builder = AgentcoreRequestBuilder()
    config = AgentcoreRequestConfig(
        message_path="query",
        history=_history_config("ctx.turns", role_path="speaker", message_path="text"),
    )
    history = [_chat_message(ChatRole.USER, "hello")]
    result = json.loads(builder.build(config, "go", history))
    assert result["ctx"]["turns"] == [{"speaker": "user", "text": "hello"}]


def test_build_history_empty_message_uses_empty_string():
    builder = AgentcoreRequestBuilder()
    config = AgentcoreRequestConfig(
        message_path="query",
        history=_history_config("messages"),
    )
    history = [ChatMessage(role=ChatRole.USER, message=None)]
    result = json.loads(builder.build(config, "hi", history))
    assert result["messages"][0]["content"] == ""


def test_build_no_history_arg_backward_compat():
    """Calling build() without history arg still works."""
    builder = AgentcoreRequestBuilder()
    config = AgentcoreRequestConfig(message_path="message")
    result = json.loads(builder.build(config, "hello"))
    assert result == {"message": "hello"}
```

- [ ] **Step 2: Run to verify they fail**

```bash
poetry run pytest tests/codemie/service/aws_bedrock/agentcore/test_agentcore_request_builder.py \
  -k "history" -v 2>&1 | tail -20
```

Expected: `TypeError: build() got an unexpected keyword argument 'history'` or similar.

- [ ] **Step 3: Update `AgentcoreRequestBuilder.build()`**

Replace the body of `build()` in `src/codemie/service/aws_bedrock/agentcore/agentcore_request_builder.py`:

```python
from typing import List, Optional

from codemie.core.constants import ChatRole
from codemie.configs import logger
from codemie.service.aws_bedrock.agentcore.agentcore_config import AgentcoreRequestConfig
from codemie.service.aws_bedrock.agentcore.utils import set_json_path


class AgentcoreRequestBuilder:
    """Builds the JSON payload sent to an AgentCore runtime endpoint.

    Serialises the user query into the structure expected by the runtime,
    using the dot-notation ``message_path`` from ``AgentcoreRequestConfig``
    to place the query at the correct key in the request body.

    When ``config.history`` is configured and a non-empty ``history`` list is
    supplied, each ``ChatMessage`` turn is serialised into a turn dict and
    written at ``config.history.history_path``.
    """

    def build(
        self,
        config: AgentcoreRequestConfig,
        user_query: str,
        history: Optional[List] = None,
    ) -> bytes:
        """Construct and serialise the runtime request payload.

        Returns UTF-8 encoded JSON with the user query at ``config.message_path``
        and, when configured, the history turns array at ``config.history.history_path``.
        """
        payload: dict = {}
        set_json_path(payload, config.message_path, user_query)

        if config.history and history:
            h = config.history
            turns = []
            for msg in history:
                turn: dict = {}
                role_label = h.user_role if msg.role == ChatRole.USER else h.assistant_role
                set_json_path(turn, h.role_path, role_label)
                set_json_path(turn, h.message_path, msg.message or "")
                turns.append(turn)
            set_json_path(payload, h.history_path, turns)

        logger.debug(
            f"[AgentCore] Request payload built: message_path={config.message_path!r}"
            f" history_turns={len(history) if config.history and history else 0}"
        )
        return json.dumps(payload).encode("utf-8")
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
poetry run pytest tests/codemie/service/aws_bedrock/agentcore/test_agentcore_request_builder.py -v 2>&1 | tail -25
```

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/codemie/service/aws_bedrock/agentcore/agentcore_request_builder.py \
        tests/codemie/service/aws_bedrock/agentcore/test_agentcore_request_builder.py
git commit -m "EPMCDME-12240: Update AgentcoreRequestBuilder to inject history turns when configured"
```

---

### Task 3: Thread history through `_build_agentcore_request` and `invoke_agentcore_runtime`

**Test-first: yes** — add a runtime service test asserting history reaches the builder.

**Files:**
- Modify: `src/codemie/service/aws_bedrock/bedrock_agentcore_runtime_service.py`
- Test: `tests/codemie/service/aws_bedrock/test_bedrock_agentcore_runtime_service.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/codemie/service/aws_bedrock/test_bedrock_agentcore_runtime_service.py`:

```python
@patch(f"{_RUNTIME_MOD}.get_setting_aws_credentials")
@patch(f"{_RUNTIME_MOD}.BedrockAgentCoreRuntimeService._bedrock_invoke_runtime")
@patch(f"{_RUNTIME_MOD}._agentcore_request_builder")
def test_invoke_agentcore_runtime_passes_history_to_builder(
    mock_builder,
    mock_bedrock_invoke_runtime,
    mock_get_setting_aws_credentials,
    mock_aws_creds,
):
    """History list is forwarded to the request builder."""
    import json
    from codemie.core.models import ChatMessage
    from codemie.core.constants import ChatRole
    from codemie.service.aws_bedrock.agentcore.agentcore_config import AgentcoreHistoryConfig

    mock_get_setting_aws_credentials.return_value = mock_aws_creds
    mock_builder.build.return_value = b'{"query":"hi","messages":[{"role":"user","content":"prev"}]}'
    mock_bedrock_invoke_runtime.return_value = b'{"output": "ok"}'

    configuration_json = json.dumps({
        "request": {
            "message_path": "query",
            "history": {"history_path": "messages"},
        },
        "response": {"streaming": False, "body": {"text_path": "output"}},
    })

    mock_assistant = MagicMock()
    mock_assistant.bedrock_agentcore_runtime.runtime_arn = "arn:test"
    mock_assistant.bedrock_agentcore_runtime.runtime_endpoint_name = "ep"
    mock_assistant.bedrock_agentcore_runtime.aws_settings_id = "setting-1"
    mock_assistant.bedrock_agentcore_runtime.configuration_json = configuration_json

    history = [ChatMessage(role=ChatRole.USER, message="prev")]

    BedrockAgentCoreRuntimeService.invoke_agentcore_runtime(
        assistant=mock_assistant,
        input_text="hi",
        conversation_id="conv-1",
        history=history,
    )

    call_kwargs = mock_builder.build.call_args
    assert call_kwargs.kwargs.get("history") == history or call_kwargs.args[2] == history
```

- [ ] **Step 2: Run to verify it fails**

```bash
poetry run pytest tests/codemie/service/aws_bedrock/test_bedrock_agentcore_runtime_service.py \
  -k "passes_history_to_builder" -v 2>&1 | tail -20
```

Expected: FAIL — `invoke_agentcore_runtime` doesn't accept `history` yet.

- [ ] **Step 3: Update `_build_agentcore_request` and `invoke_agentcore_runtime`**

In `src/codemie/service/aws_bedrock/bedrock_agentcore_runtime_service.py`:

Update `_build_agentcore_request`:
```python
@staticmethod
def _build_agentcore_request(
    config: Optional[AgentcoreConfig],
    input_text: str,
    history: Optional[List] = None,
) -> tuple[bytes, str]:
    if config is not None:
        return _agentcore_request_builder.build(config.request, input_text, history), (
            AgentcoreContentType.SSE if config.response.streaming else AgentcoreContentType.JSON
        )
    return json.dumps({"message": input_text}).encode("utf-8"), AgentcoreContentType.SSE
```

Update `invoke_agentcore_runtime` signature and the `_build_agentcore_request` call:
```python
@staticmethod
def invoke_agentcore_runtime(
    assistant: Assistant,
    input_text: str,
    conversation_id: str,
    history: Optional[List] = None,
) -> InvokeAgentCoreRuntimeResponse:
    start_time = time()

    if (
        not assistant.bedrock_agentcore_runtime
        or not assistant.bedrock_agentcore_runtime.runtime_arn
        or not assistant.bedrock_agentcore_runtime.aws_settings_id
    ):
        raise ValueError("Trying to invoke non-AgentCore runtime assistant.")

    try:
        aws_creds = get_setting_aws_credentials(assistant.bedrock_agentcore_runtime.aws_settings_id)

        config = AgentcoreConfig.parse_json(assistant.bedrock_agentcore_runtime.configuration_json)
        payload, accept = BedrockAgentCoreRuntimeService._build_agentcore_request(config, input_text, history)

        raw_response = BedrockAgentCoreRuntimeService._bedrock_invoke_runtime(
            runtime_arn=assistant.bedrock_agentcore_runtime.runtime_arn,
            qualifier=assistant.bedrock_agentcore_runtime.runtime_endpoint_name,
            payload=payload,
            accept=accept,
            region=aws_creds.region,
            access_key_id=aws_creds.access_key_id,
            secret_access_key=aws_creds.secret_access_key,
            session_token=aws_creds.session_token,
        )

        output, thoughts = BedrockAgentCoreRuntimeService._parse_agentcore_response(raw_response, config)

        return {
            "output": output,
            "thoughts": [t.model_dump() for t in thoughts],
            "time_elapsed": time() - start_time,
        }
    except ClientError as e:
        if is_resource_not_found(e):
            logger.warning(f"AgentCore runtime not found on remote: {e}")
            BedrockAgentCoreRuntimeService.validate_remote_entity_exists_and_cleanup_with_subassistants(assistant)
        else:
            logger.error(f"AWS ClientError invoking AgentCore runtime: {e}")
        return {"output": str(e), "thoughts": [], "time_elapsed": time() - start_time}
    except Exception as e:
        logger.error(f"Unexpected error invoking AgentCore runtime: {e}")
        return {"output": str(e), "thoughts": [], "time_elapsed": time() - start_time}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
poetry run pytest tests/codemie/service/aws_bedrock/test_bedrock_agentcore_runtime_service.py \
  -k "passes_history_to_builder or invoke_agentcore_runtime" -v 2>&1 | tail -20
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/codemie/service/aws_bedrock/bedrock_agentcore_runtime_service.py \
        tests/codemie/service/aws_bedrock/test_bedrock_agentcore_runtime_service.py
git commit -m "EPMCDME-12240: Thread history through _build_agentcore_request and invoke_agentcore_runtime"
```

---

### Task 4: Pass `chat_history` from `BedrockOrchestratorService`

**Test-first: yes** — verify the orchestrator forwards `chat_history` to `invoke_agentcore_runtime`.

**Files:**
- Modify: `src/codemie/service/aws_bedrock/bedrock_orchestration_service.py`
- Test: `tests/codemie/service/aws_bedrock/test_bedrock_orchestration_service.py` (create if absent)

- [ ] **Step 1: Check whether a test file for the orchestrator already exists**

```bash
ls tests/codemie/service/aws_bedrock/test_bedrock_orchestration_service.py 2>&1
```

- [ ] **Step 2: Write the failing test**

If the file does not exist, create `tests/codemie/service/aws_bedrock/test_bedrock_orchestration_service.py`. If it exists, append. Either way, add:

```python
from unittest.mock import patch, MagicMock
from codemie.rest_api.models.assistant import AssistantType
from codemie.service.aws_bedrock.bedrock_orchestration_service import BedrockOrchestratorService

_ORCHESTRATION_MOD = "codemie.service.aws_bedrock.bedrock_orchestration_service"


def _agentcore_assistant():
    a = MagicMock()
    a.type = AssistantType.BEDROCK_AGENTCORE_RUNTIME
    a.bedrock_agentcore_runtime = MagicMock()
    a.bedrock_agentcore_runtime.runtime_arn = "arn:test"
    a.bedrock = None
    return a


@patch(f"{_ORCHESTRATION_MOD}.BedrockAgentCoreRuntimeService.invoke_agentcore_runtime")
def test_orchestrator_passes_chat_history_to_agentcore(mock_invoke):
    """invoke_bedrock_assistant forwards chat_history to invoke_agentcore_runtime."""
    mock_invoke.return_value = {"output": "ok", "thoughts": [], "time_elapsed": 0.1}
    history = [MagicMock()]

    BedrockOrchestratorService.invoke_bedrock_assistant(
        assistant=_agentcore_assistant(),
        input_text="hello",
        conversation_id="conv-1",
        chat_history=history,
    )

    mock_invoke.assert_called_once_with(
        assistant=mock_invoke.call_args.kwargs["assistant"],
        input_text="hello",
        conversation_id="conv-1",
        history=history,
    )


@patch(f"{_ORCHESTRATION_MOD}.BedrockAgentCoreRuntimeService.invoke_agentcore_runtime")
def test_orchestrator_passes_none_history_when_absent(mock_invoke):
    """chat_history defaults to None and is forwarded as None."""
    mock_invoke.return_value = {"output": "ok", "thoughts": [], "time_elapsed": 0.1}

    BedrockOrchestratorService.invoke_bedrock_assistant(
        assistant=_agentcore_assistant(),
        input_text="hello",
        conversation_id="conv-1",
    )

    mock_invoke.assert_called_once_with(
        assistant=mock_invoke.call_args.kwargs["assistant"],
        input_text="hello",
        conversation_id="conv-1",
        history=None,
    )
```

- [ ] **Step 3: Run to verify it fails**

```bash
poetry run pytest tests/codemie/service/aws_bedrock/test_bedrock_orchestration_service.py \
  -k "chat_history" -v 2>&1 | tail -20
```

Expected: FAIL — `invoke_agentcore_runtime` is called without `history=`.

- [ ] **Step 4: Update `invoke_bedrock_assistant` to forward history**

In `src/codemie/service/aws_bedrock/bedrock_orchestration_service.py`, update the `BEDROCK_AGENTCORE_RUNTIME` branch:

```python
elif assistant.type == AssistantType.BEDROCK_AGENTCORE_RUNTIME:
    return BedrockAgentCoreRuntimeService.invoke_agentcore_runtime(
        assistant=assistant,
        input_text=input_text,
        conversation_id=conversation_id,
        history=chat_history,
    )
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
poetry run pytest tests/codemie/service/aws_bedrock/test_bedrock_orchestration_service.py -v 2>&1 | tail -20
```

Expected: all PASS.

- [ ] **Step 6: Run the full test suite for the affected modules**

```bash
poetry run pytest \
  tests/codemie/service/aws_bedrock/agentcore/ \
  tests/codemie/service/aws_bedrock/test_bedrock_agentcore_runtime_service.py \
  tests/codemie/service/aws_bedrock/test_bedrock_orchestration_service.py \
  -v 2>&1 | tail -30
```

Expected: all PASS, zero failures.

- [ ] **Step 7: Commit**

```bash
git add src/codemie/service/aws_bedrock/bedrock_orchestration_service.py \
        tests/codemie/service/aws_bedrock/test_bedrock_orchestration_service.py
git commit -m "EPMCDME-12240: Forward chat_history from BedrockOrchestratorService to invoke_agentcore_runtime"
```
