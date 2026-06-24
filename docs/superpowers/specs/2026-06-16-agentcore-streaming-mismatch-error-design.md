# AgentCore Streaming Mismatch Error

## Problem

When `response_config.streaming=False` (the default), the runtime sends `Accept: application/json` to AgentCore. If the endpoint returns an SSE response regardless, `AgentcoreResponseParser.parse_json` receives SSE-formatted bytes, fails to JSON-decode them, and falls back to returning the raw body string:

```python
except Exception as exc:
    logger.warning(f"[AgentCore] JSON response could not be decoded: {exc}")
    return body.decode("utf-8", errors="replace"), []  # raw "data: ..." lines surfaced to chat
```

The user sees raw `data: ...` SSE lines in the chat instead of a readable error.

## Fix

**File:** `src/codemie/service/aws_bedrock/agentcore/agentcore_response_parser.py`

Replace the silent fallback in `parse_json` with a raised `AgentcoreResponseError`:

```python
# Before
except Exception as exc:
    logger.warning(f"[AgentCore] JSON response could not be decoded: {exc}")
    return body.decode("utf-8", errors="replace"), []

# After
except Exception as exc:
    raise AgentcoreResponseError(
        "AgentCore response could not be decoded as JSON. "
        "The endpoint might be returning an invalid or streaming response — "
        "set response.streaming: true in the endpoint configuration."
    ) from exc
```

`AgentcoreResponseError` is already caught by `invoke_agentcore_runtime`'s outer `except Exception` (line 436 of `bedrock_agentcore_runtime_service.py`), which returns `{"output": str(e), ...}`. The user sees the error message in chat instead of raw SSE content.

No UI changes needed. No new exception types. No changes to the config model or request builder.

## Tests

**File:** `tests/codemie/service/aws_bedrock/agentcore/test_agentcore_response_parser.py`

Add a test that calls `parse_json` with SSE-formatted bytes and asserts it raises `AgentcoreResponseError` containing the streaming mismatch message.

## Out of scope

- `_iter_chunks` yielding raw chunks on `json.JSONDecodeError` (separate concern, streaming path).
- Legacy SSE parser path (`response_config is None`).
- UI rendering of error messages.
