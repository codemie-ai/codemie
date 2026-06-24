# AgentCore Streaming Mismatch Error Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the silent fallback in `AgentcoreResponseParser.parse_json` with a raised `AgentcoreResponseError` so that SSE bytes received when non-streaming is configured surface a readable error instead of raw `data: …` lines in chat.

**Architecture:** Single except-clause change in `parse_json`. No new types, no new imports. The existing `AgentcoreResponseError` is already imported and caught upstream by `invoke_agentcore_runtime`, which converts it to `{"output": str(e), ...}`.

**Tech Stack:** Python, pytest

---

## File Map

| File | Change |
|---|---|
| `src/codemie/service/aws_bedrock/agentcore/agentcore_response_parser.py` | Replace silent fallback in `parse_json` except clause (lines 49–51) |
| `tests/codemie/service/aws_bedrock/agentcore/test_agentcore_response_parser.py` | Add one new test; update one stale test |

---

### Task 1: Write the failing test

**Files:**
- Modify: `tests/codemie/service/aws_bedrock/agentcore/test_agentcore_response_parser.py`

- [ ] **Step 1: Add the new test at the end of the file**

Append after the last test (`test_parse_streaming_text_path_is_list_raises`):

```python
def test_parse_json_sse_body_raises_streaming_mismatch_error():
    import pytest
    from codemie.service.aws_bedrock.agentcore.agentcore_response_parser import AgentcoreResponseError

    parser = AgentcoreResponseParser()
    sse_body = b'data: {"text": "hi"}\n\ndata: {"text": "there"}\n\n'
    with pytest.raises(AgentcoreResponseError, match="streaming response"):
        parser.parse_json(sse_body, _json_response_config("output"))
```

- [ ] **Step 2: Run the test and confirm it fails**

```bash
poetry run pytest tests/codemie/service/aws_bedrock/agentcore/test_agentcore_response_parser.py::test_parse_json_sse_body_raises_streaming_mismatch_error -v
```

Expected: **FAILED** — current code returns raw string instead of raising.

---

### Task 2: Apply the fix, update the stale test, and commit

**Files:**
- Modify: `src/codemie/service/aws_bedrock/agentcore/agentcore_response_parser.py:49-51`
- Modify: `tests/codemie/service/aws_bedrock/agentcore/test_agentcore_response_parser.py:174-179`

- [ ] **Step 1: Replace the silent fallback in `parse_json`**

In `src/codemie/service/aws_bedrock/agentcore/agentcore_response_parser.py`, change lines 49–51:

```python
        # Before
        except Exception as exc:
            logger.warning(f"[AgentCore] JSON response could not be decoded: {exc}")
            return body.decode("utf-8", errors="replace"), []
```

to:

```python
        # After
        except Exception as exc:
            raise AgentcoreResponseError(
                "AgentCore response could not be decoded as JSON. "
                "The endpoint might be returning an invalid or streaming response — "
                "set response.streaming: true in the endpoint configuration."
            ) from exc
```

- [ ] **Step 2: Update the stale test that asserted the old fallback behaviour**

`test_parse_json_malformed_body_returns_raw_string` (line 174) currently expects a raw string return. Update it to assert the error is raised:

```python
def test_parse_json_malformed_body_raises():
    import pytest
    from codemie.service.aws_bedrock.agentcore.agentcore_response_parser import AgentcoreResponseError

    parser = AgentcoreResponseParser()
    body = b"not valid json"
    with pytest.raises(AgentcoreResponseError, match="streaming response"):
        parser.parse_json(body, _json_response_config("output"))
```

- [ ] **Step 3: Run the new test and confirm it passes**

```bash
poetry run pytest tests/codemie/service/aws_bedrock/agentcore/test_agentcore_response_parser.py::test_parse_json_sse_body_raises_streaming_mismatch_error -v
```

Expected: **PASSED**

- [ ] **Step 4: Run the full test module and confirm no regressions**

```bash
poetry run pytest tests/codemie/service/aws_bedrock/agentcore/test_agentcore_response_parser.py -v
```

Expected: all tests **PASSED**

- [ ] **Step 5: Commit**

```bash
git add src/codemie/service/aws_bedrock/agentcore/agentcore_response_parser.py \
        tests/codemie/service/aws_bedrock/agentcore/test_agentcore_response_parser.py
git commit -m "EPMCDME-12240: Raise AgentcoreResponseError on SSE response when streaming not configured"
```
