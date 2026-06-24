# AgentCore Invocation Fix & Configuration Expansion — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix broken AgentCore invocation (ARN bug), replace the fragile `__QUERY_PLACEHOLDER__` mechanism with a structured `configuration_json` schema, and wire thought emission into the existing Thought pipeline.

**Architecture:** Three new focused modules (`agentcore_config.py`, `agentcore_request_builder.py`, `agentcore_response_parser.py`) own all schema, building, and parsing logic. The service layer calls them and emits thoughts via `StreamedGenerationResult` — no parsing logic leaks into services or routers.

**Tech Stack:** Python 3.11+, Pydantic v2, pytest, boto3 (bedrock-agentcore)

---

## File Map

| Action | Path | Responsibility |
|---|---|---|
| Create | `src/codemie/service/aws_bedrock/agentcore/agentcore_config.py` | Pydantic schema models + `parse_configuration_json` |
| Create | `src/codemie/service/aws_bedrock/agentcore/agentcore_request_builder.py` | `resolve_path`, `set_path`, `AgentcoreRequestBuilder` |
| Create | `src/codemie/service/aws_bedrock/agentcore/agentcore_response_parser.py` | `AgentcoreResponseParser` (JSON + SSE) |
| Create | `tests/codemie/service/aws_bedrock/agentcore/__init__.py` | Test package marker |
| Create | `tests/codemie/service/aws_bedrock/agentcore/test_agentcore_config.py` | Config model tests |
| Create | `tests/codemie/service/aws_bedrock/agentcore/test_agentcore_request_builder.py` | Builder tests |
| Create | `tests/codemie/service/aws_bedrock/agentcore/test_agentcore_response_parser.py` | Parser tests |
| Modify | `src/codemie/rest_api/models/assistant.py:252` | `BedrockAgentcoreRuntimeData`: add `configuration_json`, keep `invocation_json` as legacy alias |
| Modify | `src/codemie/rest_api/models/vendor.py:40` | `ImportAgentcoreRuntime`: make `configuration_json` optional |
| Modify | `src/codemie/service/aws_bedrock/bedrock_orchestration_service.py:93` | Fix ARN field in `is_bedrock_assistant()` |
| Modify | `src/codemie/service/aws_bedrock/bedrock_agentcore_runtime_service.py:353` | Fix ARN guard; wire new parsers; extend response type |
| Modify | `src/codemie/service/aws_bedrock/agentcore/bedrock_agentcore_endpoint_service.py:373` | Remove placeholder logic; use `AgentcoreRequestBuilder` |
| Modify | `src/codemie/agents/assistant_agent.py:661` | Emit thought frames from AgentCore response |
| Modify | `tests/codemie/service/aws_bedrock/test_bedrock_agentcore_runtime_service.py` | Update invocation tests |

---

## Task 1: Schema models (`agentcore_config.py`)

**Files:**
- Create: `src/codemie/service/aws_bedrock/agentcore/agentcore_config.py`
- Create: `tests/codemie/service/aws_bedrock/agentcore/__init__.py`
- Create: `tests/codemie/service/aws_bedrock/agentcore/test_agentcore_config.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/codemie/service/aws_bedrock/agentcore/test_agentcore_config.py
import json
import pytest
from codemie.service.aws_bedrock.agentcore.agentcore_config import (
    AgentcoreConfig,
    AgentcoreBodyConfig,
    AgentcoreReasoningConfig,
    parse_configuration_json,
)


def test_non_streaming_requires_body():
    with pytest.raises(Exception):
        AgentcoreConfig.model_validate({
            "request": {"message_path": "input"},
            "response": {"streaming": False},  # body missing
        })


def test_streaming_requires_chunk():
    with pytest.raises(Exception):
        AgentcoreConfig.model_validate({
            "request": {"message_path": "input"},
            "response": {"streaming": True},  # chunk missing
        })


def test_valid_non_streaming_config():
    cfg = AgentcoreConfig.model_validate({
        "request": {"message_path": "input"},
        "response": {"streaming": False, "body": {"text_path": "output"}},
    })
    assert cfg.response.streaming is False
    assert cfg.response.body.text_path == "output"
    assert cfg.response.chunk is None


def test_valid_streaming_config():
    cfg = AgentcoreConfig.model_validate({
        "request": {"message_path": "prompt"},
        "response": {"streaming": True, "chunk": {"text_path": "delta"}},
    })
    assert cfg.response.streaming is True
    assert cfg.response.chunk.text_path == "delta"


def test_reasoning_requires_text_path_and_active_path():
    with pytest.raises(Exception):
        AgentcoreBodyConfig.model_validate({
            "text_path": "output",
            "reasoning": {"text_path": "thinking"},  # active_path missing
        })


def test_parse_configuration_json_new_format():
    raw = json.dumps({
        "request": {"message_path": "input"},
        "response": {"streaming": False, "body": {"text_path": "output"}},
    })
    cfg = parse_configuration_json(raw)
    assert cfg is not None
    assert cfg.request.message_path == "input"


def test_parse_configuration_json_legacy_format_returns_none():
    raw = '{"message": "__QUERY_PLACEHOLDER__"}'
    assert parse_configuration_json(raw) is None


def test_parse_configuration_json_none_input():
    assert parse_configuration_json(None) is None
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
pytest tests/codemie/service/aws_bedrock/agentcore/test_agentcore_config.py -v
```
Expected: `ModuleNotFoundError` or collection error — module doesn't exist yet.

- [ ] **Step 3: Create the test package marker**

```python
# tests/codemie/service/aws_bedrock/agentcore/__init__.py
```
(empty file)

- [ ] **Step 4: Implement `agentcore_config.py`**

```python
# src/codemie/service/aws_bedrock/agentcore/agentcore_config.py
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

import json
from typing import Optional

from pydantic import BaseModel, model_validator


class AgentcoreReasoningConfig(BaseModel):
    text_path: str
    active_path: str
    name_path: Optional[str] = None
    args_path: Optional[str] = None


class AgentcoreBodyConfig(BaseModel):
    text_path: str
    reasoning: Optional[AgentcoreReasoningConfig] = None


class AgentcoreResponseConfig(BaseModel):
    streaming: bool = False
    body: Optional[AgentcoreBodyConfig] = None
    chunk: Optional[AgentcoreBodyConfig] = None

    @model_validator(mode="after")
    def _validate_body_or_chunk(self) -> "AgentcoreResponseConfig":
        if not self.streaming and self.body is None:
            raise ValueError("body is required when streaming is False")
        if self.streaming and self.chunk is None:
            raise ValueError("chunk is required when streaming is True")
        return self


class AgentcoreRequestConfig(BaseModel):
    message_path: str = "message"


class AgentcoreConfig(BaseModel):
    request: AgentcoreRequestConfig = AgentcoreRequestConfig()
    response: AgentcoreResponseConfig


def parse_configuration_json(raw: Optional[str]) -> Optional[AgentcoreConfig]:
    """Return AgentcoreConfig for new-format JSON, or None for legacy/empty input."""
    if not raw:
        return None
    try:
        data = json.loads(raw)
        if "response" not in data:
            return None
        return AgentcoreConfig.model_validate(data)
    except Exception:
        return None
```

- [ ] **Step 5: Run tests — expect pass**

```bash
pytest tests/codemie/service/aws_bedrock/agentcore/test_agentcore_config.py -v
```
Expected: all 8 tests PASS.

- [ ] **Step 6: Lint**

```bash
make ruff
```

- [ ] **Step 7: Commit**

```bash
git add src/codemie/service/aws_bedrock/agentcore/agentcore_config.py \
        tests/codemie/service/aws_bedrock/agentcore/__init__.py \
        tests/codemie/service/aws_bedrock/agentcore/test_agentcore_config.py
git commit -m "EPMCDME-12240: Add AgentcoreConfig schema models and parse_configuration_json"
```

---

## Task 2: Request builder (`agentcore_request_builder.py`)

**Files:**
- Create: `src/codemie/service/aws_bedrock/agentcore/agentcore_request_builder.py`
- Create: `tests/codemie/service/aws_bedrock/agentcore/test_agentcore_request_builder.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/codemie/service/aws_bedrock/agentcore/test_agentcore_request_builder.py
import json
import pytest
from codemie.service.aws_bedrock.agentcore.agentcore_request_builder import (
    resolve_path,
    set_path,
    AgentcoreRequestBuilder,
)
from codemie.service.aws_bedrock.agentcore.agentcore_config import AgentcoreRequestConfig


# --- resolve_path ---

def test_resolve_path_top_level():
    assert resolve_path({"output": "hello"}, "output") == "hello"


def test_resolve_path_nested():
    assert resolve_path({"result": {"answer": "hi"}}, "result.answer") == "hi"


def test_resolve_path_list_index():
    assert resolve_path({"choices": ["a", "b"]}, "choices.0") == "a"


def test_resolve_path_deeply_nested():
    data = {"a": {"b": {"c": "deep"}}}
    assert resolve_path(data, "a.b.c") == "deep"


def test_resolve_path_missing_key_returns_none():
    assert resolve_path({"a": 1}, "b") is None


def test_resolve_path_none_data_returns_none():
    assert resolve_path(None, "a") is None


def test_resolve_path_empty_path_returns_none():
    assert resolve_path({"a": 1}, "") is None


# --- set_path ---

def test_set_path_top_level():
    d = {}
    set_path(d, "message", "hello")
    assert d == {"message": "hello"}


def test_set_path_nested():
    d = {}
    set_path(d, "input.query", "hello")
    assert d == {"input": {"query": "hello"}}


def test_set_path_overwrites_existing():
    d = {"message": "old"}
    set_path(d, "message", "new")
    assert d["message"] == "new"


# --- AgentcoreRequestBuilder ---

def test_builder_simple_path():
    builder = AgentcoreRequestBuilder()
    config = AgentcoreRequestConfig(message_path="message")
    result = json.loads(builder.build(config, "hello world"))
    assert result == {"message": "hello world"}


def test_builder_nested_path():
    builder = AgentcoreRequestBuilder()
    config = AgentcoreRequestConfig(message_path="input.query")
    result = json.loads(builder.build(config, "test query"))
    assert result == {"input": {"query": "test query"}}


def test_builder_returns_bytes():
    builder = AgentcoreRequestBuilder()
    config = AgentcoreRequestConfig(message_path="prompt")
    assert isinstance(builder.build(config, "q"), bytes)
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
pytest tests/codemie/service/aws_bedrock/agentcore/test_agentcore_request_builder.py -v
```
Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement `agentcore_request_builder.py`**

```python
# src/codemie/service/aws_bedrock/agentcore/agentcore_request_builder.py
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

import json
from typing import Any, Optional

from codemie.service.aws_bedrock.agentcore.agentcore_config import AgentcoreRequestConfig


def resolve_path(data: Any, path: Optional[str]) -> Any:
    """Resolve a dot-notation path from a dict/list. Returns None if absent."""
    if not path or data is None:
        return None
    current = data
    for part in path.split("."):
        if current is None:
            return None
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list):
            try:
                current = current[int(part)]
            except (ValueError, IndexError):
                return None
        else:
            return None
    return current


def set_path(data: dict, path: str, value: Any) -> None:
    """Set a value at a dot-notation path, creating intermediate dicts as needed."""
    parts = path.split(".")
    current = data
    for part in parts[:-1]:
        if part not in current or not isinstance(current[part], dict):
            current[part] = {}
        current = current[part]
    current[parts[-1]] = value


class AgentcoreRequestBuilder:
    def build(self, config: AgentcoreRequestConfig, user_query: str) -> bytes:
        payload: dict = {}
        set_path(payload, config.message_path, user_query)
        return json.dumps(payload).encode("utf-8")
```

- [ ] **Step 4: Run tests — expect pass**

```bash
pytest tests/codemie/service/aws_bedrock/agentcore/test_agentcore_request_builder.py -v
```
Expected: all 12 tests PASS.

- [ ] **Step 5: Lint and commit**

```bash
make ruff
git add src/codemie/service/aws_bedrock/agentcore/agentcore_request_builder.py \
        tests/codemie/service/aws_bedrock/agentcore/test_agentcore_request_builder.py
git commit -m "EPMCDME-12240: Add AgentcoreRequestBuilder with dot-notation path resolution"
```

---

## Task 3: Response parser (`agentcore_response_parser.py`)

**Files:**
- Create: `src/codemie/service/aws_bedrock/agentcore/agentcore_response_parser.py`
- Create: `tests/codemie/service/aws_bedrock/agentcore/test_agentcore_response_parser.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/codemie/service/aws_bedrock/agentcore/test_agentcore_response_parser.py
import json
import pytest
from unittest.mock import MagicMock
from codemie.service.aws_bedrock.agentcore.agentcore_config import (
    AgentcoreBodyConfig,
    AgentcoreReasoningConfig,
    AgentcoreResponseConfig,
)
from codemie.service.aws_bedrock.agentcore.agentcore_response_parser import AgentcoreResponseParser


def _json_response_config(text_path="output", reasoning=None):
    return AgentcoreResponseConfig(
        streaming=False,
        body=AgentcoreBodyConfig(text_path=text_path, reasoning=reasoning),
    )


def _streaming_config(text_path="delta", reasoning=None):
    return AgentcoreResponseConfig(
        streaming=True,
        chunk=AgentcoreBodyConfig(text_path=text_path, reasoning=reasoning),
    )


def _sse_stream(lines: list[str]):
    """Mock SSE stream from list of data payloads."""
    raw = b"\n".join(
        (f"data: {line}".encode() if line else b"")
        for line in lines
    )
    mock = MagicMock()
    mock.iter_lines.return_value = [
        (f"data: {line}".encode() if line else b"")
        for line in lines
    ]
    return mock


# --- parse_json ---

def test_parse_json_simple():
    parser = AgentcoreResponseParser()
    body = json.dumps({"output": "hello"}).encode()
    text, thoughts = parser.parse_json(body, _json_response_config("output"))
    assert text == "hello"
    assert thoughts == []


def test_parse_json_nested_path():
    parser = AgentcoreResponseParser()
    body = json.dumps({"result": {"answer": "hi"}}).encode()
    text, thoughts = parser.parse_json(body, _json_response_config("result.answer"))
    assert text == "hi"


def test_parse_json_missing_path_returns_empty():
    parser = AgentcoreResponseParser()
    body = json.dumps({"other": "value"}).encode()
    text, thoughts = parser.parse_json(body, _json_response_config("output"))
    assert text == ""


def test_parse_json_with_reasoning():
    parser = AgentcoreResponseParser()
    body = json.dumps({"output": "answer", "thinking": "my reasoning"}).encode()
    reasoning = AgentcoreReasoningConfig(text_path="thinking", active_path="unused")
    text, thoughts = parser.parse_json(body, _json_response_config("output", reasoning))
    assert text == "answer"
    assert len(thoughts) == 1
    assert thoughts[0].message == "my reasoning"
    assert thoughts[0].in_progress is False


def test_parse_json_reasoning_with_name_and_args():
    parser = AgentcoreResponseParser()
    body = json.dumps({
        "output": "done",
        "thinking": "reasoning text",
        "tool": "SearchTool",
        "args": {"q": "weather"},
    }).encode()
    reasoning = AgentcoreReasoningConfig(
        text_path="thinking", active_path="unused",
        name_path="tool", args_path="args",
    )
    text, thoughts = parser.parse_json(body, _json_response_config("output", reasoning))
    assert thoughts[0].author_name == "SearchTool"
    assert '"q": "weather"' in thoughts[0].input_text


# --- parse_streaming ---

def test_parse_streaming_raw_text():
    parser = AgentcoreResponseParser()
    # chunk.text_path is set but data lines are raw text (not JSON) — falls back
    config = AgentcoreResponseConfig(
        streaming=True,
        chunk=AgentcoreBodyConfig(text_path="text"),
    )
    stream = _sse_stream(["Hello", " world", ""])
    text, thoughts = parser.parse_streaming(stream, config)
    assert "Hello" in text
    assert thoughts == []


def test_parse_streaming_json_chunks():
    parser = AgentcoreResponseParser()
    config = _streaming_config("delta")
    stream = _sse_stream([
        json.dumps({"delta": "Hello"}),
        json.dumps({"delta": " world"}),
        "",
    ])
    text, thoughts = parser.parse_streaming(stream, config)
    assert text == "Hello world"


def test_parse_streaming_thoughts_in_progress():
    parser = AgentcoreResponseParser()
    reasoning = AgentcoreReasoningConfig(text_path="thinking", active_path="active")
    config = _streaming_config("text", reasoning)
    stream = _sse_stream([
        json.dumps({"thinking": "step 1", "active": True}),
        json.dumps({"thinking": " step 2", "active": True}),
        json.dumps({"thinking": "done", "active": False}),
        json.dumps({"text": "answer"}),
        "",
    ])
    text, thoughts = parser.parse_streaming(stream, config)
    assert text == "answer"
    in_progress = [t for t in thoughts if t.in_progress]
    closed = [t for t in thoughts if not t.in_progress]
    assert len(in_progress) >= 1
    assert len(closed) >= 1


def test_parse_streaming_thought_closes_at_stream_end():
    parser = AgentcoreResponseParser()
    reasoning = AgentcoreReasoningConfig(text_path="thinking", active_path="active")
    config = _streaming_config("text", reasoning)
    stream = _sse_stream([
        json.dumps({"thinking": "still thinking", "active": True}),
        "",
    ])
    text, thoughts = parser.parse_streaming(stream, config)
    assert any(not t.in_progress for t in thoughts)


def test_parse_streaming_skips_non_data_lines():
    parser = AgentcoreResponseParser()
    config = _streaming_config("delta")
    mock_stream = MagicMock()
    mock_stream.iter_lines.return_value = [
        b"event: message",
        b"data: " + json.dumps({"delta": "hi"}).encode(),
        b"",
    ]
    text, thoughts = parser.parse_streaming(mock_stream, config)
    assert text == "hi"
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
pytest tests/codemie/service/aws_bedrock/agentcore/test_agentcore_response_parser.py -v
```
Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement `agentcore_response_parser.py`**

```python
# src/codemie/service/aws_bedrock/agentcore/agentcore_response_parser.py
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

import json
import uuid
from typing import Optional

from codemie.chains.base import Thought, ThoughtAuthorType
from codemie.configs import logger
from codemie.service.aws_bedrock.agentcore.agentcore_config import AgentcoreBodyConfig, AgentcoreResponseConfig
from codemie.service.aws_bedrock.agentcore.agentcore_request_builder import resolve_path


class AgentcoreResponseParser:

    def parse_json(self, body: bytes, config: AgentcoreResponseConfig) -> tuple[str, list[Thought]]:
        """Parse a full JSON response body. Returns (text, thoughts)."""
        try:
            data = json.loads(body.decode("utf-8"))
        except Exception:
            return body.decode("utf-8", errors="replace"), []

        body_config = config.body
        text = str(resolve_path(data, body_config.text_path) or "")
        thoughts = []
        if body_config.reasoning:
            thought = self._extract_reasoning(data, body_config, in_progress=False)
            if thought:
                thoughts.append(thought)
        return text, thoughts

    def parse_streaming(self, stream, config: AgentcoreResponseConfig) -> tuple[str, list[Thought]]:
        """Parse an SSE stream. Returns (accumulated_text, all_thought_frames)."""
        chunk_config = config.chunk
        content_parts: list[str] = []
        thoughts: list[Thought] = []
        current_thought: Optional[Thought] = None

        for line in stream.iter_lines(chunk_size=256):
            if not line:
                continue
            line_str = line.decode("utf-8") if isinstance(line, bytes) else line
            if not line_str.startswith("data: "):
                continue
            data_str = line_str[6:]

            if chunk_config.text_path:
                try:
                    data = json.loads(data_str)
                except json.JSONDecodeError:
                    content_parts.append(data_str)
                    continue

                if chunk_config.reasoning:
                    active = resolve_path(data, chunk_config.reasoning.active_path)
                    if active is True:
                        thought_text = str(resolve_path(data, chunk_config.reasoning.text_path) or "")
                        name = resolve_path(data, chunk_config.reasoning.name_path) if chunk_config.reasoning.name_path else None
                        args = resolve_path(data, chunk_config.reasoning.args_path) if chunk_config.reasoning.args_path else None
                        if current_thought is None:
                            current_thought = Thought(
                                id=str(uuid.uuid4()),
                                in_progress=True,
                                message=thought_text,
                                author_name=str(name) if name else None,
                                input_text=json.dumps(args) if args is not None else None,
                                author_type=ThoughtAuthorType.Agent,
                            )
                        else:
                            current_thought.message = (current_thought.message or "") + thought_text
                        thoughts.append(current_thought.model_copy(update={"in_progress": True}))
                        continue
                    elif active is False and current_thought is not None:
                        thoughts.append(current_thought.model_copy(update={"in_progress": False}))
                        current_thought = None
                        continue

                text = resolve_path(data, chunk_config.text_path)
                if text is not None:
                    content_parts.append(str(text))
            else:
                content_parts.append(data_str)

        if current_thought is not None:
            thoughts.append(current_thought.model_copy(update={"in_progress": False}))

        return "".join(content_parts), thoughts

    def _extract_reasoning(
        self, data: dict, body_config: AgentcoreBodyConfig, in_progress: bool
    ) -> Optional[Thought]:
        """Extract a single Thought from a data dict using body_config.reasoning paths."""
        if not body_config.reasoning:
            return None
        text = resolve_path(data, body_config.reasoning.text_path)
        if text is None:
            return None
        name = resolve_path(data, body_config.reasoning.name_path) if body_config.reasoning.name_path else None
        args = resolve_path(data, body_config.reasoning.args_path) if body_config.reasoning.args_path else None
        return Thought(
            id=str(uuid.uuid4()),
            in_progress=in_progress,
            message=str(text),
            author_name=str(name) if name else None,
            input_text=json.dumps(args) if args is not None else None,
            author_type=ThoughtAuthorType.Agent,
        )
```

- [ ] **Step 4: Run tests — expect pass**

```bash
pytest tests/codemie/service/aws_bedrock/agentcore/test_agentcore_response_parser.py -v
```
Expected: all 11 tests PASS.

- [ ] **Step 5: Lint and commit**

```bash
make ruff
git add src/codemie/service/aws_bedrock/agentcore/agentcore_response_parser.py \
        tests/codemie/service/aws_bedrock/agentcore/test_agentcore_response_parser.py
git commit -m "EPMCDME-12240: Add AgentcoreResponseParser for JSON and SSE streaming with thought support"
```

---

## Task 4: ARN bug fix

**Files:**
- Modify: `src/codemie/service/aws_bedrock/bedrock_orchestration_service.py:93`
- Modify: `src/codemie/service/aws_bedrock/bedrock_agentcore_runtime_service.py:363`

- [ ] **Step 1: Write failing test**

```python
# Add to tests/codemie/service/aws_bedrock/test_bedrock_agentcore_runtime_service.py

from unittest.mock import MagicMock, patch
from codemie.rest_api.models.assistant import Assistant, AssistantType, BedrockAgentcoreRuntimeData
from codemie.service.aws_bedrock.bedrock_orchestration_service import BedrockOrchestratorService


def _make_agentcore_assistant(runtime_arn="arn:aws:bedrock:us-east-1:123:runtime/r1", endpoint_arn=None):
    assistant = MagicMock(spec=Assistant)
    assistant.type = AssistantType.BEDROCK_AGENTCORE_RUNTIME
    assistant.bedrock = None
    rt = MagicMock(spec=BedrockAgentcoreRuntimeData)
    rt.runtime_arn = runtime_arn
    rt.runtime_endpoint_arn = endpoint_arn
    rt.aws_settings_id = "setting-1"
    assistant.bedrock_agentcore_runtime = rt
    return assistant


def test_is_bedrock_assistant_uses_runtime_arn():
    """is_bedrock_assistant must check runtime_arn, not runtime_endpoint_arn."""
    assistant = _make_agentcore_assistant(
        runtime_arn="arn:aws:bedrock:us-east-1:123:runtime/r1",
        endpoint_arn=None,  # endpoint_arn absent
    )
    assert BedrockOrchestratorService.is_bedrock_assistant(assistant) is True


def test_is_bedrock_assistant_false_when_no_runtime_arn():
    assistant = _make_agentcore_assistant(runtime_arn=None, endpoint_arn="some-arn")
    assert BedrockOrchestratorService.is_bedrock_assistant(assistant) is False
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
pytest tests/codemie/service/aws_bedrock/test_bedrock_agentcore_runtime_service.py::test_is_bedrock_assistant_uses_runtime_arn -v
```
Expected: FAIL — currently checks `runtime_endpoint_arn`, not `runtime_arn`.

- [ ] **Step 3: Fix `is_bedrock_assistant()` in `bedrock_orchestration_service.py:93`**

```python
# Before (line 93):
or (assistant.bedrock_agentcore_runtime and assistant.bedrock_agentcore_runtime.runtime_endpoint_arn)

# After:
or (assistant.bedrock_agentcore_runtime and assistant.bedrock_agentcore_runtime.runtime_arn)
```

- [ ] **Step 4: Fix guard in `invoke_agentcore_runtime()` at `bedrock_agentcore_runtime_service.py:363`**

```python
# Before (line 363):
not assistant.bedrock_agentcore_runtime.runtime_endpoint_arn

# After:
not assistant.bedrock_agentcore_runtime.runtime_arn
```

- [ ] **Step 5: Run tests — expect pass**

```bash
pytest tests/codemie/service/aws_bedrock/test_bedrock_agentcore_runtime_service.py::test_is_bedrock_assistant_uses_runtime_arn tests/codemie/service/aws_bedrock/test_bedrock_agentcore_runtime_service.py::test_is_bedrock_assistant_false_when_no_runtime_arn -v
```
Expected: both PASS.

- [ ] **Step 6: Run full suite to check no regressions**

```bash
pytest tests/codemie/service/aws_bedrock/test_bedrock_agentcore_runtime_service.py -v
```

- [ ] **Step 7: Lint and commit**

```bash
make ruff
git add src/codemie/service/aws_bedrock/bedrock_orchestration_service.py \
        src/codemie/service/aws_bedrock/bedrock_agentcore_runtime_service.py \
        tests/codemie/service/aws_bedrock/test_bedrock_agentcore_runtime_service.py
git commit -m "EPMCDME-12240: Fix ARN guard — use runtime_arn consistently in is_bedrock_assistant and invoke guard"
```

---

## Task 5: Update `BedrockAgentcoreRuntimeData` model

**Files:**
- Modify: `src/codemie/rest_api/models/assistant.py:252`

The field `invocation_json` must become `configuration_json`. Old DB records already stored under the `invocation_json` key must still load correctly — a `model_validator` migrates the value on read without touching the DB.

- [ ] **Step 1: Write failing test**

```python
# Add to tests/codemie/service/aws_bedrock/test_bedrock_agentcore_runtime_service.py

from codemie.rest_api.models.assistant import BedrockAgentcoreRuntimeData


def test_bedrock_agentcore_runtime_data_reads_configuration_json():
    data = BedrockAgentcoreRuntimeData(
        runtime_id="r1", runtime_arn="arn:...", runtime_endpoint_id="ep1",
        runtime_endpoint_arn="arn:...:ep", runtime_endpoint_name="ep",
        runtime_endpoint_live_version="1", aws_settings_id="s1",
        configuration_json='{"response": {"streaming": false, "body": {"text_path": "output"}}}',
    )
    assert data.configuration_json is not None


def test_bedrock_agentcore_runtime_data_migrates_legacy_invocation_json():
    """Old DB records have invocation_json; model must migrate to configuration_json."""
    data = BedrockAgentcoreRuntimeData.model_validate({
        "runtime_id": "r1", "runtime_arn": "arn:...", "runtime_endpoint_id": "ep1",
        "runtime_endpoint_arn": "arn:...:ep", "runtime_endpoint_name": "ep",
        "runtime_endpoint_live_version": "1", "aws_settings_id": "s1",
        "invocation_json": '{"message": "__QUERY_PLACEHOLDER__"}',
    })
    assert data.configuration_json == '{"message": "__QUERY_PLACEHOLDER__"}'
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
pytest tests/codemie/service/aws_bedrock/test_bedrock_agentcore_runtime_service.py::test_bedrock_agentcore_runtime_data_reads_configuration_json tests/codemie/service/aws_bedrock/test_bedrock_agentcore_runtime_service.py::test_bedrock_agentcore_runtime_data_migrates_legacy_invocation_json -v
```
Expected: FAIL — `configuration_json` field doesn't exist yet.

- [ ] **Step 3: Update `BedrockAgentcoreRuntimeData` in `assistant.py:252`**

```python
# src/codemie/rest_api/models/assistant.py  (replace lines 252–261)
from pydantic import model_validator

class BedrockAgentcoreRuntimeData(BaseModel):
    runtime_id: str
    runtime_arn: str
    runtime_endpoint_id: str
    runtime_endpoint_arn: str
    runtime_endpoint_name: str
    runtime_endpoint_live_version: str
    runtime_endpoint_description: Optional[str] = None
    aws_settings_id: str
    configuration_json: Optional[str] = None
    invocation_json: Optional[str] = None  # legacy field — migrated on load

    @model_validator(mode="after")
    def _migrate_invocation_json(self) -> "BedrockAgentcoreRuntimeData":
        if self.configuration_json is None and self.invocation_json is not None:
            self.configuration_json = self.invocation_json
        return self
```

- [ ] **Step 4: Run tests — expect pass**

```bash
pytest tests/codemie/service/aws_bedrock/test_bedrock_agentcore_runtime_service.py::test_bedrock_agentcore_runtime_data_reads_configuration_json tests/codemie/service/aws_bedrock/test_bedrock_agentcore_runtime_service.py::test_bedrock_agentcore_runtime_data_migrates_legacy_invocation_json -v
```

- [ ] **Step 5: Lint and commit**

```bash
make ruff
git add src/codemie/rest_api/models/assistant.py \
        tests/codemie/service/aws_bedrock/test_bedrock_agentcore_runtime_service.py
git commit -m "EPMCDME-12240: Add configuration_json to BedrockAgentcoreRuntimeData with legacy invocation_json migration"
```

---

## Task 6: Wire parsers into the invocation service

**Files:**
- Modify: `src/codemie/service/aws_bedrock/bedrock_agentcore_runtime_service.py:353`
- Modify: `src/codemie/service/aws_bedrock/agentcore/bedrock_agentcore_endpoint_service.py:373`

Replace the old `_prepare_invocation_payload`, `_validate_invocation_json`, `_contains_placeholder`, and `_replace_placeholder_in_structure` methods. Wire `AgentcoreRequestBuilder`, `AgentcoreResponseParser`, and `parse_configuration_json` into the invocation and import flows.

- [ ] **Step 1: Write failing tests**

```python
# Add to tests/codemie/service/aws_bedrock/test_bedrock_agentcore_runtime_service.py

from unittest.mock import patch, MagicMock
from codemie.rest_api.models.assistant import BedrockAgentcoreRuntimeData
from codemie.service.aws_bedrock.bedrock_agentcore_runtime_service import BedrockAgentCoreRuntimeService
import json


def _make_assistant_with_config(configuration_json: str):
    assistant = MagicMock()
    rt = MagicMock(spec=BedrockAgentcoreRuntimeData)
    rt.runtime_arn = "arn:aws:bedrock:us-east-1:123:runtime/r1"
    rt.runtime_endpoint_name = "my-endpoint"
    rt.aws_settings_id = "setting-1"
    rt.configuration_json = configuration_json
    assistant.bedrock_agentcore_runtime = rt
    return assistant


@patch("codemie.service.aws_bedrock.bedrock_agentcore_runtime_service.get_setting_aws_credentials")
@patch("codemie.service.aws_bedrock.bedrock_agentcore_runtime_service.BedrockAgentCoreRuntimeService._bedrock_invoke_runtime")
def test_invoke_uses_new_config_request_path(mock_invoke, mock_creds):
    mock_creds.return_value = MagicMock(
        region="us-east-1", access_key_id="k", secret_access_key="s", session_token=None
    )
    config_json = json.dumps({
        "request": {"message_path": "prompt"},
        "response": {"streaming": False, "body": {"text_path": "answer"}},
    })
    mock_invoke.return_value = json.dumps({"answer": "hello"}).encode()
    assistant = _make_assistant_with_config(config_json)

    result = BedrockAgentCoreRuntimeService.invoke_agentcore_runtime(
        assistant=assistant, input_text="my question", conversation_id="conv-1"
    )
    # Verify request payload used message_path="prompt"
    call_kwargs = mock_invoke.call_args
    payload = json.loads(call_kwargs.kwargs.get("payload") or call_kwargs.args[2])
    assert payload == {"prompt": "my question"}
    assert result["output"] == "hello"


@patch("codemie.service.aws_bedrock.bedrock_agentcore_runtime_service.get_setting_aws_credentials")
@patch("codemie.service.aws_bedrock.bedrock_agentcore_runtime_service.BedrockAgentCoreRuntimeService._bedrock_invoke_runtime")
def test_invoke_streaming_uses_text_event_stream_accept(mock_invoke, mock_creds):
    mock_creds.return_value = MagicMock(
        region="us-east-1", access_key_id="k", secret_access_key="s", session_token=None
    )
    config_json = json.dumps({
        "request": {"message_path": "message"},
        "response": {"streaming": True, "chunk": {"text_path": "delta"}},
    })
    mock_stream = MagicMock()
    mock_stream.iter_lines.return_value = [
        b'data: {"delta": "hello"}',
        b"",
    ]
    mock_invoke.return_value = mock_stream
    assistant = _make_assistant_with_config(config_json)

    BedrockAgentCoreRuntimeService.invoke_agentcore_runtime(
        assistant=assistant, input_text="q", conversation_id="c"
    )
    call_kwargs = mock_invoke.call_args
    assert call_kwargs.kwargs.get("accept") == "text/event-stream"
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
pytest tests/codemie/service/aws_bedrock/test_bedrock_agentcore_runtime_service.py::test_invoke_uses_new_config_request_path tests/codemie/service/aws_bedrock/test_bedrock_agentcore_runtime_service.py::test_invoke_streaming_uses_text_event_stream_accept -v
```

- [ ] **Step 3: Update `invoke_agentcore_runtime` in `bedrock_agentcore_runtime_service.py`**

Replace lines 353–408. The updated method:
1. Parses `configuration_json` via `parse_configuration_json`
2. Builds payload via `AgentcoreRequestBuilder` (new path) or falls back to legacy `_prepare_invocation_payload`
3. Determines `accept` header from `config.response.streaming`
4. Calls `_bedrock_invoke_runtime` with the dynamic `accept`
5. Parses response via `AgentcoreResponseParser` and returns `output` + `thoughts`

```python
# In bedrock_agentcore_runtime_service.py — replace invoke_agentcore_runtime method

from codemie.service.aws_bedrock.agentcore.agentcore_config import parse_configuration_json
from codemie.service.aws_bedrock.agentcore.agentcore_request_builder import AgentcoreRequestBuilder
from codemie.service.aws_bedrock.agentcore.agentcore_response_parser import AgentcoreResponseParser

_request_builder = AgentcoreRequestBuilder()
_response_parser = AgentcoreResponseParser()


class InvokeAgentCoreRuntimeResponse(TypedDict):
    output: str
    thoughts: list  # list of Thought.model_dump() dicts
    time_elapsed: float


@staticmethod
def invoke_agentcore_runtime(
    assistant: Assistant,
    input_text: str,
    conversation_id: str,
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
        rt = assistant.bedrock_agentcore_runtime
        config = parse_configuration_json(rt.configuration_json)

        if config:
            payload = _request_builder.build(config.request, input_text)
            accept = "text/event-stream" if config.response.streaming else "application/json"
        else:
            # Legacy __QUERY_PLACEHOLDER__ fallback
            payload = BedrockAgentCoreEndpointService._prepare_invocation_payload(
                invocation_json=rt.configuration_json,
                query=input_text,
                conversation_id=conversation_id,
            )
            accept = "text/event-stream"

        raw_response = BedrockAgentCoreRuntimeService._bedrock_invoke_runtime(
            runtime_arn=rt.runtime_arn,
            qualifier=rt.runtime_endpoint_name,
            payload=payload,
            accept=accept,
            region=aws_creds.region,
            access_key_id=aws_creds.access_key_id,
            secret_access_key=aws_creds.secret_access_key,
            session_token=aws_creds.session_token,
        )

        if config:
            if config.response.streaming:
                output, thoughts = _response_parser.parse_streaming(raw_response, config.response)
            else:
                output, thoughts = _response_parser.parse_json(raw_response, config.response)
        else:
            output = BedrockAgentCoreRuntimeService._parse_response_by_content_type(
                raw_response, raw_response.get("contentType", "") if isinstance(raw_response, dict) else ""
            )
            thoughts = []

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

- [ ] **Step 4: Update `_bedrock_invoke_runtime` to accept the `accept` parameter**

```python
# bedrock_agentcore_runtime_service.py — update _bedrock_invoke_runtime signature

@staticmethod
def _bedrock_invoke_runtime(
    runtime_arn: str,
    qualifier: str,
    payload: bytes,
    region: str,
    access_key_id: str,
    secret_access_key: str,
    accept: str = "text/event-stream",   # new param with backward-compat default
    session_token: Optional[str] = None,
):
    def _func(client):
        response = client.invoke_agent_runtime(
            agentRuntimeArn=runtime_arn,
            qualifier=qualifier,
            payload=payload,
            contentType="application/json",
            accept=accept,              # dynamic
        )
        if "application/json" in accept:
            body = response.get("response") or response.get("Body")
            if body:
                return body.read() if hasattr(body, "read") else body
            return b""
        return response.get("response") or response.get("Body")

    client = get_aws_client_for_service(
        "bedrock-agentcore",
        region=region,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        session_token=session_token,
    )
    return handle_aws_call(_func, client)
```

- [ ] **Step 5: Update `_create_assistant_data` in `bedrock_agentcore_endpoint_service.py` to store `configuration_json`**

```python
# bedrock_agentcore_endpoint_service.py — in _create_assistant_data (line ~493)
# Replace: "invocation_json": invocation_json,
# With:    "configuration_json": invocation_json,
```

- [ ] **Step 6: Run tests — expect pass**

```bash
pytest tests/codemie/service/aws_bedrock/test_bedrock_agentcore_runtime_service.py -v
```

- [ ] **Step 7: Lint and commit**

```bash
make ruff
git add src/codemie/service/aws_bedrock/bedrock_agentcore_runtime_service.py \
        src/codemie/service/aws_bedrock/agentcore/bedrock_agentcore_endpoint_service.py
git commit -m "EPMCDME-12240: Wire AgentcoreRequestBuilder and AgentcoreResponseParser into invocation; dynamic accept header"
```

---

## Task 7: Emit thoughts in `assistant_agent.py`

**Files:**
- Modify: `src/codemie/agents/assistant_agent.py:661`

- [ ] **Step 1: Write failing test**

```python
# Add to tests/codemie/service/aws_bedrock/test_bedrock_agentcore_runtime_service.py

from unittest.mock import patch, MagicMock, call
from codemie.agents.assistant_agent import AIToolsAgent
from codemie.chains.base import Thought, StreamedGenerationResult, ThoughtAuthorType
from codemie.rest_api.models.assistant import AssistantType
import json


def _make_streaming_agent(response_output: str, thoughts_dicts: list):
    agent = MagicMock(spec=AIToolsAgent)
    agent.assistant = MagicMock()
    agent.assistant.type = AssistantType.BEDROCK_AGENTCORE_RUNTIME
    agent.conversation_id = "conv-1"
    agent.request = MagicMock()
    agent.request.text = "hello"
    agent.thread_generator = MagicMock()

    with patch(
        "codemie.agents.assistant_agent.BedrockOrchestratorService.is_bedrock_assistant",
        return_value=True,
    ), patch(
        "codemie.agents.assistant_agent.BedrockOrchestratorService.invoke_bedrock_assistant",
        return_value={"output": response_output, "thoughts": thoughts_dicts, "time_elapsed": 0.1},
    ):
        chunks = []
        AIToolsAgent._agent_streaming(agent, chunks)
    return agent, chunks


def test_agent_streaming_emits_thought_frames_before_output():
    thought_dict = Thought(
        id="t1", in_progress=False, message="reasoning", author_type=ThoughtAuthorType.Agent
    ).model_dump()
    agent, chunks = _make_streaming_agent("the answer", [thought_dict])

    send_calls = agent.thread_generator.send.call_args_list
    assert len(send_calls) >= 1
    first_sent = send_calls[0][0][0]
    parsed = StreamedGenerationResult.model_validate_json(first_sent)
    assert parsed.thought is not None
    assert parsed.thought.message == "reasoning"


def test_agent_streaming_no_thoughts_no_send():
    agent, chunks = _make_streaming_agent("the answer", [])
    agent.thread_generator.send.assert_not_called()
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
pytest tests/codemie/service/aws_bedrock/test_bedrock_agentcore_runtime_service.py::test_agent_streaming_emits_thought_frames_before_output tests/codemie/service/aws_bedrock/test_bedrock_agentcore_runtime_service.py::test_agent_streaming_no_thoughts_no_send -v
```

- [ ] **Step 3: Update `_agent_streaming` in `assistant_agent.py:660`**

```python
# assistant_agent.py — replace the bedrock branch in _agent_streaming (lines 661–673)

def _agent_streaming(self, chunks_collector: List[str]):
    if self.assistant and BedrockOrchestratorService.is_bedrock_assistant(self.assistant):
        logger.info(f"Streaming Bedrock assistant output for AssistantId={self.assistant.id}")
        try:
            response = BedrockOrchestratorService.invoke_bedrock_assistant(
                assistant=self.assistant,
                input_text=self.request.text or "",
                conversation_id=self.conversation_id,
            )
            for thought_dict in response.get("thoughts", []):
                self.thread_generator.send(
                    StreamedGenerationResult(
                        thought=Thought(**thought_dict)
                    ).model_dump_json()
                )
            AIToolsAgent.process_output(response["output"], chunks_collector)
        except Exception as e:
            logger.error(f"Error during Bedrock assistant invocation: {str(e)}", exc_info=True)
        return
    # ... rest of method unchanged
```

Add imports at the top of `assistant_agent.py` if not already present:
```python
from codemie.chains.base import Thought, StreamedGenerationResult
```

- [ ] **Step 4: Run tests — expect pass**

```bash
pytest tests/codemie/service/aws_bedrock/test_bedrock_agentcore_runtime_service.py::test_agent_streaming_emits_thought_frames_before_output tests/codemie/service/aws_bedrock/test_bedrock_agentcore_runtime_service.py::test_agent_streaming_no_thoughts_no_send -v
```

- [ ] **Step 5: Lint and commit**

```bash
make ruff
git add src/codemie/agents/assistant_agent.py \
        tests/codemie/service/aws_bedrock/test_bedrock_agentcore_runtime_service.py
git commit -m "EPMCDME-12240: Emit Thought frames from AgentCore streaming response in _agent_streaming"
```

---

## Task 8: Update entity response model

**Files:**
- Modify: `src/codemie/service/aws_bedrock/agentcore/bedrock_agentcore_endpoint_service.py:270`

`AgentcoreEndpointEntity.configurationJson` currently reads from `rt.invocation_json`. Update it to read from `rt.configuration_json`.

- [ ] **Step 1: Write failing test**

```python
# Add to tests/codemie/service/aws_bedrock/test_bedrock_agentcore_runtime_service.py

from codemie.service.aws_bedrock.agentcore.bedrock_agentcore_endpoint_service import (
    BedrockAgentCoreEndpointService,
)


def test_endpoint_entity_exposes_configuration_json():
    """_build_endpoint_entity must read configuration_json, not invocation_json."""
    mock_assistant = MagicMock()
    rt = MagicMock()
    rt.runtime_endpoint_id = "ep-1"
    rt.runtime_endpoint_name = "my-endpoint"
    rt.runtime_endpoint_live_version = "2"
    rt.runtime_endpoint_description = "desc"
    rt.configuration_json = '{"response": {"streaming": false, "body": {"text_path": "output"}}}'
    rt.invocation_json = None
    mock_assistant.id = "assistant-uuid"
    mock_assistant.bedrock_agentcore_runtime = rt

    entity = BedrockAgentCoreEndpointService._build_imported_endpoint_entity(mock_assistant)
    assert entity.configurationJson == rt.configuration_json
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
pytest tests/codemie/service/aws_bedrock/test_bedrock_agentcore_runtime_service.py::test_endpoint_entity_exposes_configuration_json -v
```

- [ ] **Step 3: Update the entity builder in `bedrock_agentcore_endpoint_service.py`**

Find all occurrences where `configurationJson=rt.invocation_json` is set (lines ~280, ~310) and replace with `configurationJson=rt.configuration_json`.

```python
# Line ~280:
# Before: configurationJson=rt.invocation_json,
# After:  configurationJson=rt.configuration_json,

# Line ~310 (second occurrence):
# Before: configurationJson=invocation_json,
# After:  configurationJson=configuration_json,
# (Also rename the local variable from invocation_json to configuration_json where used)
```

- [ ] **Step 4: Run tests — expect pass**

```bash
pytest tests/codemie/service/aws_bedrock/test_bedrock_agentcore_runtime_service.py -v
```

- [ ] **Step 5: Run full test suite**

```bash
make test
```

- [ ] **Step 6: Lint and commit**

```bash
make ruff
git add src/codemie/service/aws_bedrock/agentcore/bedrock_agentcore_endpoint_service.py \
        tests/codemie/service/aws_bedrock/test_bedrock_agentcore_runtime_service.py
git commit -m "EPMCDME-12240: Update entity builder to read configuration_json instead of invocation_json"
```

---

## Task 9: Final verification

- [ ] **Run full test suite**

```bash
make test
```
Expected: all tests PASS, no regressions.

- [ ] **Run lint**

```bash
make ruff
```

- [ ] **Run verify gate**

```bash
make verify
```

- [ ] **Smoke-check the new schema end-to-end** — confirm `parse_configuration_json` → `AgentcoreRequestBuilder.build` → `AgentcoreResponseParser.parse_json/parse_streaming` all chain correctly:

```bash
python -c "
import json
from codemie.service.aws_bedrock.agentcore.agentcore_config import parse_configuration_json
from codemie.service.aws_bedrock.agentcore.agentcore_request_builder import AgentcoreRequestBuilder
from codemie.service.aws_bedrock.agentcore.agentcore_response_parser import AgentcoreResponseParser

raw = json.dumps({
    'request': {'message_path': 'input'},
    'response': {'streaming': False, 'body': {'text_path': 'output'}},
})
cfg = parse_configuration_json(raw)
payload = AgentcoreRequestBuilder().build(cfg.request, 'hello world')
print('Payload:', payload)

body = json.dumps({'output': 'the answer'}).encode()
text, thoughts = AgentcoreResponseParser().parse_json(body, cfg.response)
print('Text:', text, '| Thoughts:', thoughts)
"
```
Expected:
```
Payload: b'{"input": "hello world"}'
Text: the answer | Thoughts: []
```
