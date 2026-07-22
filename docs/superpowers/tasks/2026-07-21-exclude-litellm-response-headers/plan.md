# Exclude LiteLLM Response Headers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Filter out LiteLLM-specific response headers (x-litellm-*) before returning proxy responses to clients.

**Architecture:** Modify the response header filtering in `_proxy_to_llm_proxy` to exclude all headers starting with `x-litellm-`. The existing `PROXY_RESPONSE_HOP_BY_HOP_HEADERS` set filters standard hop-by-hop headers; we'll add a prefix-based check to also filter LiteLLM internal headers. Single change point at line 1371 covers all three response paths (usage-tracking stream, passthrough stream, error response).

**Tech Stack:** FastAPI, Starlette, httpx, pytest

---

## File Structure

**Modified files:**
- `src/codemie/enterprise/litellm/proxy_router.py` — add LiteLLM header filtering in response construction
- `tests/enterprise/litellm/test_proxy_router.py` — add test cases for LiteLLM header filtering

**No new files created.**

---

###Task 1: Add LiteLLM response header filtering

**Files:**
- Modify: `src/codemie/enterprise/litellm/proxy_router.py:1370-1372`
- Test: `tests/enterprise/litellm/test_proxy_router.py`

Test-first: yes — test_proxy_response_filters_litellm_headers

- [ ] **Step 1: Write the failing test for LiteLLM header filtering**

Add this test to `tests/enterprise/litellm/test_proxy_router.py` in a new test class at the end of the file:

```python
class TestProxyResponseHeaderFiltering:
    """Test response header filtering from LiteLLM."""

    @pytest.mark.asyncio
    async def test_proxy_response_filters_litellm_headers(self):
        """Test that x-litellm-* headers are filtered from proxy responses."""
        # Create mock request
        mock_request = MagicMock()
        mock_request.url = MagicMock()
        mock_request.url.path = "/v1/chat/completions"
        mock_request.method = "POST"
        mock_request.headers = Headers({"content-type": "application/json"})
        
        # Mock request body
        async def mock_body():
            return b'{"model": "gpt-4", "messages": []}'
        mock_request.body = mock_body
        
        # Create mock downstream response with LiteLLM headers
        mock_downstream_response = MagicMock()
        mock_downstream_response.status_code = 200
        mock_downstream_response.headers = httpx.Headers({
            "content-type": "application/json",
            "x-litellm-call-id": "test-call-id",
            "x-litellm-version": "1.83.7",
            "x-litellm-response-cost": "0.0002244",
            "x-litellm-model-id": "claude-4-5-sonnet",
            "x-litellm-key-spend": "602.80",
            "x-custom-header": "should-keep",
        })
        
        # Mock the streaming response
        async def mock_aiter():
            yield b'{"choices": []}'
        mock_downstream_response.aiter_bytes = mock_aiter
        
        with patch("codemie.enterprise.litellm.proxy_router.config") as mock_config:
            mock_config.LLM_PROXY_TRACK_USAGE = False
            mock_config.LITE_LLM_APP_KEY = "test-key"
            mock_config.LITE_LLM_PROXY_APP_KEY = ""
            
            with patch("codemie.enterprise.litellm.proxy_router.get_llm_proxy_client") as mock_client:
                mock_http_client = AsyncMock()
                mock_http_client.build_request.return_value = MagicMock()
                mock_http_client.send.return_value = mock_downstream_response
                mock_client.return_value.__aenter__.return_value = mock_http_client
                
                with patch("codemie.enterprise.litellm.proxy_router.litellm_context") as mock_ctx:
                    mock_ctx.get.side_effect = LookupError()
                    
                    # Call the proxy function
                    result = await _proxy_to_llm_proxy(mock_request, None, None, None)
        
        # Verify LiteLLM headers are filtered out
        assert isinstance(result, StreamingResponse)
        result_headers = dict(result.headers)
        
        # All x-litellm-* headers should be filtered
        assert "x-litellm-call-id" not in result_headers
        assert "x-litellm-version" not in result_headers
        assert "x-litellm-response-cost" not in result_headers
        assert "x-litellm-model-id" not in result_headers
        assert "x-litellm-key-spend" not in result_headers
        
        # Other headers should pass through
        assert "content-type" in result_headers
        assert "x-custom-header" in result_headers
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/enterprise/litellm/test_proxy_router.py::TestProxyResponseHeaderFiltering::test_proxy_response_filters_litellm_headers -xvs`

Expected: FAIL — x-litellm-* headers are present in result (not filtered)

- [ ] **Step 3: Implement LiteLLM header filtering**

In `src/codemie/enterprise/litellm/proxy_router.py`, find the response_headers construction around line 1370-1372 and modify it to filter x-litellm-* headers:

```python
# Build response headers, filtering hop-by-hop headers and LiteLLM internal headers
response_headers = {
    k: v
    for k, v in downstream_response.headers.items()
    if k.lower() not in PROXY_RESPONSE_HOP_BY_HOP_HEADERS and not k.lower().startswith("x-litellm-")
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/enterprise/litellm/test_proxy_router.py::TestProxyResponseHeaderFiltering::test_proxy_response_filters_litellm_headers -xvs`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/codemie/enterprise/litellm/proxy_router.py tests/enterprise/litellm/test_proxy_router.py
git commit -m "EPMCDME-13640: Filter x-litellm-* headers from proxy responses"
```

---

### Task 2: Test usage-tracking path with LiteLLM headers

**Files:**
- Test: `tests/enterprise/litellm/test_proxy_router.py`

Test-first: yes — test_proxy_response_filters_litellm_headers_with_usage_tracking

- [ ] **Step 1: Write the failing test for usage-tracking path**

Add this test to the `TestProxyResponseHeaderFiltering` class:

```python
@pytest.mark.asyncio
async def test_proxy_response_filters_litellm_headers_with_usage_tracking(self):
    """Test that x-litellm-* headers are filtered even with usage tracking enabled."""
    mock_request = MagicMock()
    mock_request.url = MagicMock()
    mock_request.url.path = "/v1/chat/completions"
    mock_request.method = "POST"
    mock_request.headers = Headers({"content-type": "application/json"})
    
    async def mock_body():
        return b'{"model": "gpt-4", "messages": []}'
    mock_request.body = mock_body
    
    # Response body with usage info
    response_body = b'{"choices": [], "usage": {"total_tokens": 100}}'
    
    mock_downstream_response = MagicMock()
    mock_downstream_response.status_code = 200
    mock_downstream_response.headers = httpx.Headers({
        "content-type": "application/json",
        "x-litellm-call-id": "test-call-id",
        "x-litellm-response-cost": "0.0002244",
        "x-litellm-response-cost-original": "0.0002244",
    })
    
    async def mock_aiter():
        yield response_body
    mock_downstream_response.aiter_bytes = mock_aiter
    
    with patch("codemie.enterprise.litellm.proxy_router.config") as mock_config:
        mock_config.LLM_PROXY_TRACK_USAGE = True
        mock_config.LITE_LLM_APP_KEY = "test-key"
        mock_config.LITE_LLM_PROXY_APP_KEY = ""
        
        with patch("codemie.enterprise.litellm.proxy_router.get_llm_proxy_client") as mock_client:
            mock_http_client = AsyncMock()
            mock_http_client.build_request.return_value = MagicMock()
            mock_http_client.send.return_value = mock_downstream_response
            mock_client.return_value.__aenter__.return_value = mock_http_client
            
            with patch("codemie.enterprise.litellm.proxy_router.litellm_context") as mock_ctx:
                mock_ctx.get.side_effect = LookupError()
                
                with patch("codemie.enterprise.litellm.proxy_router.LLMProxyMonitoringService"):
                    # Call the proxy function
                    result = await _proxy_to_llm_proxy(mock_request, None, None, None)
    
    # Verify LiteLLM headers are filtered even with usage tracking
    assert isinstance(result, StreamingResponse)
    result_headers = dict(result.headers)
    
    assert "x-litellm-call-id" not in result_headers
    assert "x-litellm-response-cost" not in result_headers
    assert "x-litellm-response-cost-original" not in result_headers
    assert "content-type" in result_headers
```

- [ ] **Step 2: Run test to verify it passes**

Run: `pytest tests/enterprise/litellm/test_proxy_router.py::TestProxyResponseHeaderFiltering::test_proxy_response_filters_litellm_headers_with_usage_tracking -xvs`

Expected: PASS (implementation from Task 1 already covers this path)

- [ ] **Step 3: Commit**

```bash
git add tests/enterprise/litellm/test_proxy_router.py
git commit -m "EPMCDME-13640: Add usage-tracking path test for LiteLLM header filtering"
```

---

### Task 3: Test error response path

**Files:**
- Test: `tests/enterprise/litellm/test_proxy_router.py`

Test-first: yes — test_error_response_filters_litellm_headers

- [ ] **Step 1: Write the failing test for error response path**

Add this test to the `TestProxyResponseHeaderFiltering` class:

```python
@pytest.mark.asyncio
async def test_error_response_filters_litellm_headers(self):
    """Test that x-litellm-* headers are filtered from error responses."""
    mock_request = MagicMock()
    mock_request.url = MagicMock()
    mock_request.url.path = "/v1/chat/completions"
    mock_request.method = "POST"
    mock_request.headers = Headers({"content-type": "application/json"})
    
    async def mock_body():
        return b'{"model": "gpt-4", "messages": []}'
    mock_request.body = mock_body
    
    # Mock error response with LiteLLM headers
    mock_downstream_response = MagicMock()
    mock_downstream_response.status_code = 500
    mock_downstream_response.headers = httpx.Headers({
        "content-type": "application/json",
        "x-litellm-call-id": "error-call-id",
        "x-litellm-version": "1.83.7",
    })
    
    async def mock_aiter():
        yield b'{"error": {"message": "Internal error"}}'
    mock_downstream_response.aiter_bytes = mock_aiter
    
    with patch("codemie.enterprise.litellm.proxy_router.config") as mock_config:
        mock_config.LLM_PROXY_TRACK_USAGE = False
        mock_config.LITE_LLM_APP_KEY = "test-key"
        mock_config.LITE_LLM_PROXY_APP_KEY = ""
        
        with patch("codemie.enterprise.litellm.proxy_router.get_llm_proxy_client") as mock_client:
            mock_http_client = AsyncMock()
            mock_http_client.build_request.return_value = MagicMock()
            mock_http_client.send.return_value = mock_downstream_response
            mock_client.return_value.__aenter__.return_value = mock_http_client
            
            with patch("codemie.enterprise.litellm.proxy_router.litellm_context") as mock_ctx:
                mock_ctx.get.side_effect = LookupError()
                
                # Call the proxy function
                result = await _proxy_to_llm_proxy(mock_request, None, None, None)
    
    # Verify LiteLLM headers are filtered from error responses
    assert isinstance(result, StreamingResponse)
    result_headers = dict(result.headers)
    
    assert "x-litellm-call-id" not in result_headers
    assert "x-litellm-version" not in result_headers
    assert "content-type" in result_headers
```

- [ ] **Step 2: Run test to verify it passes**

Run: `pytest tests/enterprise/litellm/test_proxy_router.py::TestProxyResponseHeaderFiltering::test_error_response_filters_litellm_headers -xvs`

Expected: PASS (implementation from Task 1 already covers this path)

- [ ] **Step 3: Commit**

```bash
git add tests/enterprise/litellm/test_proxy_router.py
git commit -m "EPMCDME-13640: Add error response path test for LiteLLM header filtering"
```

---

### Task 4: Run full test suite

**Files:**
- All test files

- [ ] **Step 1: Run all proxy router tests**

Run: `pytest tests/enterprise/litellm/test_proxy_router.py -v`

Expected: All tests PASS

- [ ] **Step 2: Run linter**

Run: `make ruff`

Expected: No errors

- [ ] **Step 3: Final commit if any lint fixes needed**

```bash
git add .
git commit -m "EPMCDME-13640: Fix linting issues"
```

---

## Self-Review Checklist

**Spec coverage:**
- ✓ Exclude LiteLLM-specific headers from proxy responses — Task 1
- ✓ Filter headers from successful responses — Task 1
- ✓ Filter headers with usage tracking enabled — Task 2
- ✓ Filter headers from error responses — Task 3
- ✓ Use prefix-based filtering (future-proof) — Task 1
- ✓ Regression tests for all response paths — Tasks 1, 2, 3

**Placeholder scan:** None found

**Type consistency:**
- `response_headers` is consistently `dict[str, str]`
- `downstream_response.headers` is consistently `httpx.Headers`
- All test mocks use `httpx.Headers()` or `starlette.datastructures.Headers()` consistently

**Critical implementation details:**
- ✓ Single change point (line 1371) covers all three response paths
- ✓ Prefix-based filtering (`startswith("x-litellm-")`) is future-proof
- ✓ Lowercase comparison (`k.lower()`) matches existing pattern
- ✓ Filtering happens after internal reads of `x-litellm-response-cost` (line 1025)
- ✓ Non-LiteLLM headers pass through unchanged
