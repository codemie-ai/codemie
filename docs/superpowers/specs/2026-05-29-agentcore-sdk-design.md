# Design — AgentCore Python SDK

**Date:** 2026-05-29  
**Status:** Draft

---

## Problem

Runtime authors integrating with CodeMie via AgentCore must manually construct `configuration_json` to tell CodeMie how to parse their responses. This is error-prone and unnecessary for the common case.

---

## Goal

A minimal Python package (`codemie-agentcore`) that provides standard response types. Their field names match CodeMie's default parser paths — so zero `configuration_json` is needed out of the box.

**The SDK is types + serialization only.** No HTTP, no framework, no base classes to subclass.

---

## Types

```python
from typing import Optional
from pydantic import BaseModel

class Thought(BaseModel):
    text: str
    in_progress: bool = False
    name: Optional[str] = None   # author or tool name
    args: Optional[str] = None   # tool arguments (JSON string)

class Request(BaseModel):
    message: str        # user query, extracted from request body
    session_id: str
    raw: dict           # full request body for custom access

class Response(BaseModel):
    content: str
    thought: Optional[Thought] = None

class ResponseChunk(BaseModel):
    content: Optional[str] = None
    thought: Optional[Thought] = None
```

---

## Serialization

Types use Pydantic's `model_dump(exclude_none=True)` to produce the JSON shape CodeMie's parser expects:

```python
Response(content="hello").model_dump(exclude_none=True)
# {"content": "hello"}

Response(content="hello", thought=Thought(text="thinking", in_progress=False)).model_dump(exclude_none=True)
# {"content": "hello", "thought": {"text": "thinking", "in_progress": false}}
```

`thought` is omitted when `None`. `name` and `args` are omitted when `None`. `exclude_none=True` handles this automatically.

The streaming handler yields `chunk.model_dump_json(exclude_none=True)` as the SSE `data:` payload per chunk.

---

## Request Parsing

```python
from codemie_agentcore import parse_request

request = parse_request(body)   # extracts message from body["message"], session_id from body["session_id"]
```

---

## Default Configuration

The SDK exports `DEFAULT_CONFIG` — the `configuration_json` CodeMie uses when none is supplied at import time. It matches the field paths above.

```python
DEFAULT_CONFIG_JSON = {
    "request": {"message_path": "message"},
    "response": {
        "streaming": False,
        "body": {
            "text_path": "content",
            "reasoning": {
                "text_path": "thought.text",
                "active_path": "thought.in_progress",
                "name_path": "thought.name",
                "args_path": "thought.args"
            }
        }
    }
}

DEFAULT_CONFIG_STREAMING = {
    "request": {"message_path": "message"},
    "response": {
        "streaming": True,
        "chunk": {
            "text_path": "content",
            "reasoning": {
                "text_path": "thought.text",
                "active_path": "thought.in_progress",
                "name_path": "thought.name",
                "args_path": "thought.args"
            }
        }
    }
}
```

CodeMie adds `codemie-agentcore` as a dependency and imports these constants directly — the paths are never duplicated.

---

## Usage Example

```python
from codemie_agentcore import parse_request, Response, ResponseChunk, Thought
import json

# Lambda / FastAPI handler — runtime author writes this
def handler(event, context):
    request = parse_request(json.loads(event["body"]))
    answer = my_model.generate(request.message)
    return {"body": json.dumps(Response(content=answer).to_dict())}

# Streaming
def stream_handler(event, context):
    request = parse_request(json.loads(event["body"]))
    def generate():
        for chunk in my_model.stream(request.message):
            yield f"data: {ResponseChunk(content=chunk.text).model_dump_json(exclude_none=True)}\n\n"
    return StreamingResponse(generate())
```

---

## Custom Endpoints

If the runtime cannot use these types (e.g. an existing endpoint with a fixed response shape), the operator supplies `configuration_json` manually in CodeMie at import time. The SDK is not involved.

---

## Package

| Field | Value |
|---|---|
| Package name | `codemie-agentcore` |
| Distribution | Internal GitLab registry |
| Python | ≥ 3.10 |
| Dependencies | `pydantic>=2.0` |

---

## What Changes in CodeMie

1. `bedrock_agentcore_runtime_service.py` — when `configuration_json` is absent, apply `DEFAULT_CONFIG_JSON` or `DEFAULT_CONFIG_STREAMING` from the SDK package (based on `accept` header) instead of the current plain `{"message": input_text}` fallback.
2. `pyproject.toml` — add `codemie-agentcore` dependency.
