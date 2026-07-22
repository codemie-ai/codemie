# Technical Research

**Task**: proxy response headers litellm filtering
**Generated**: 2026-07-21T00:00:00Z
**Research path**: filesystem

---

## 1. Original Context

Fix EPMCDME-13640: Exclude LiteLLM-specific response headers from proxy endpoint responses. The problem is that when LiteLLM returns a response, it includes internal headers like x-litellm-call-id, x-litellm-version, x-litellm-response-cost, etc. These headers should be filtered out before the response reaches the client. This is about RESPONSE header filtering (outbound: LiteLLM → CodeMie Proxy → Client), not request header filtering.

---

## 2. Codebase Findings

### Existing Implementations

- `src/codemie/enterprise/litellm/proxy_router.py` — the sole file requiring change; contains `PROXY_RESPONSE_HOP_BY_HOP_HEADERS` (lines 131–140), the `response_headers` dict construction (lines 1370–1372), and all three response-return branches (usage-tracking `StreamingResponse`, passthrough `StreamingResponse`, `_handle_error_response`)
- `src/codemie/enterprise/litellm/llm_factory.py` — generates outbound REQUEST headers (`x-litellm-tags`); also reads `x-litellm-response-cost` from embedding responses internally (line 69) — this is an internal LangChain LLM call path, not the proxy path
- `src/codemie/enterprise/litellm/llm_proxy_provider_adapter.py` — thin `LLMProxyProvider` wrapper; no header logic
- `src/codemie/configs/config.py` — all `LITELLM_*` and `LLM_PROXY_*` env-var declarations; no header-filter config currently
- `src/codemie/agents/callbacks/tokens_callback.py` — reads `x-litellm-response-cost` from LangChain generation info in the internal LLM call path; unaffected by this change

**Key filter structures in `proxy_router.py`:**

- `PROXY_HOP_BY_HOP_HEADERS` (lines 102–126): request-direction filter (inbound, client → LiteLLM); contains standard RFC 2616 hop-by-hop headers only
- `PROXY_RESPONSE_HOP_BY_HOP_HEADERS` (lines 131–140): response-direction filter (outbound, LiteLLM → client); currently contains only standard RFC 2616 hop-by-hop headers — **this is the exact fix location**
- `_sanitize_local_response_headers()` (line 143): strips `content-length` and `content-encoding` from locally-constructed error `Response` objects only; not used for streamed proxy responses

**Critical ordering detail**: `x-litellm-response-cost` is consumed internally at `proxy_router.py:1025` (during the `LLM_PROXY_TRACK_USAGE` streaming path) and at `llm_factory.py:69` (embedding path) — both reads happen **before** the `response_headers` dict is built at line 1371. It is safe to exclude this header from the outbound dict.

**Single filter application point**: the dict comprehension at lines 1370–1372 (`{k: v for k, v in downstream_response.headers.items() if k.lower() not in PROXY_RESPONSE_HOP_BY_HOP_HEADERS}`) is the sole construction point for `response_headers`. This dict is reused by all three response-return branches — expanding the set covers all paths simultaneously.

### Architecture and Layers Affected

- **FastAPI Router layer** (`proxy_router.py`): `_proxy_to_llm_proxy`, `_create_proxy_endpoint`, `register_proxy_endpoints` — this is the only layer affected
- **Response construction sub-layer** within the proxy router: the `response_headers` dict built from `downstream_response.headers` at line 1371

No service layer, repository layer, database layer, or external integration layer is affected.

### Integration Points

- **Internal**: `proxy_router` → `llm_factory` (outbound request headers only; unaffected)
- **Internal**: `proxy_router` → `client` (`httpx.AsyncClient` via `get_llm_proxy_client`; response headers come from `downstream_response.headers` which is an `httpx.Headers` instance)
- **Internal**: `proxy_router` → `LLMProxyMonitoringService` (background task; unaffected)
- **External**: upstream LiteLLM server at `LITE_LLM_URL` — source of the headers being filtered

### Patterns and Conventions

- **Two-set pattern**: `PROXY_HOP_BY_HOP_HEADERS` (request-direction) and `PROXY_RESPONSE_HOP_BY_HOP_HEADERS` (response-direction) are intentionally kept separate; do not merge them
- **Lowercase convention**: all entries in both sets must be lowercase — the filter uses `k.lower()` for case-insensitive matching (documented at `proxy_router.py:113–114`)
- **Set membership check**: filter applied as `k.lower() not in <set>` in a dict comprehension; extending the set is the idiomatic fix — no helper function, decorator, or middleware is needed
- The existing pattern supports either explicit header names or could support prefix-based logic if a new helper function is introduced

---

## 3. Documentation Findings

### Guides and Architecture Docs

- `.ai-run/guides/integration/llm-providers.md` — directly relevant; documents LiteLLM enterprise proxy, `x-litellm-tags` injection, and "keep the two paths separate" decision (EPMCDME-12602); covers REQUEST-side header injection only — no guidance on RESPONSE header filtering
- `.ai-run/guides/api/endpoint-conventions.md` — general REST conventions; no proxy or LiteLLM content
- `.ai-run/guides/development/security-patterns.md` — states never log full auth headers; tangentially relevant (same principle: do not leak internal proxy headers to clients)

### Architectural Decisions

- RFC 2616 hop-by-hop header decision recorded in inline comment at `proxy_router.py:100`: "HTTP headers that should NOT be forwarded between proxies."
- Lowercase-key convention recorded at `proxy_router.py:113–114`.
- Decision in `llm-providers.md` (EPMCDME-12602): "LiteLLM path uses `x-litellm-tags`; keep the two paths separate." — applies to request headers only.
- No ADR or recorded decision exists for response-side LiteLLM header filtering — this task introduces that policy.

### Derived Conventions

- Extend `PROXY_RESPONSE_HOP_BY_HOP_HEADERS` (not `PROXY_HOP_BY_HOP_HEADERS`) for response-direction changes.
- All new header name strings must be lowercase.
- No new helper function or middleware is required given the existing single-point filter pattern.

---

## 4. Testing Landscape

### Existing Coverage

- `tests/enterprise/litellm/test_proxy_router.py` — comprehensive coverage of `_read_request_body`, `_extract_model`, `_extract_request_info`, `_prepare_proxy_headers` (REQUEST-side filtering), `_proxy_to_llm_proxy`, `_handle_error_response`, `_streaming_response_with_usage_tracking`, `register_proxy_endpoints`, CLI version checks, budget identity resolution. **No test for response-direction LiteLLM header filtering.**
- `tests/enterprise/litellm/test_llm_factory.py` — `x-litellm-tags` request header generation, LiteLLM model factory wrappers
- `tests/enterprise/litellm/test_embedding_wrapper.py` — reads `x-litellm-response-cost` from raw embedding response headers (internal call path, not proxy path)
- `tests/codemie/agents/callbacks/test_tokens_callback.py` — reads `x-litellm-response-cost` in token cost callback (internal call path)
- `tests/codemie/service/test_custom_headers_producer.py` — `x-litellm-tags` header production
- `tests/codemie/service/monitoring/test_llm_proxy_monitoring_service.py` — LLM proxy monitoring service

### Testing Framework and Patterns

- pytest 8.3.x + pytest-asyncio 0.23.x + pytest-mock 3.14.x + pytest-httpx 0.35.x
- `unittest.mock.patch` as context manager (nested and stacked) for dependency injection
- `AsyncMock` for coroutines (`body`, `aread`, `aclose`, `send`, service calls)
- `MagicMock` for sync objects (request, user, response)
- `httpx.Headers` and `starlette.datastructures.Headers` used directly to construct realistic header objects in tests
- `monkeypatch` for module-level attribute patching
- Class-based test grouping (`class TestXxx`) with one `@pytest.mark.asyncio async def test_*` method per scenario
- `pytest.raises(HTTPException)` for expected error scenarios
- Session-scoped `autouse` fixture in `tests/conftest.py` mocking DB engine

### Coverage Gaps

- No test asserts that `x-litellm-call-id`, `x-litellm-version`, `x-litellm-response-cost`, `x-litellm-model-id`, `x-litellm-key-alias`, `x-litellm-end-user`, or similar `x-litellm-*` headers are absent from the `StreamingResponse` returned by `_proxy_to_llm_proxy`
- No test verifies that `_streaming_response_with_usage_tracking` strips these headers (the `TestStreamingResponseWithUsageTracking` class asserts on chunk count and background tasks, but never inspects the `headers` parameter passed to `StreamingResponse`)
- No test verifies the non-streaming (non-error) response path strips these headers
- `TestHandleErrorResponse` tests check `content-length`/`content-encoding` stripping via `_sanitize_local_response_headers`, but never assert `x-litellm-*` headers are absent from error responses that pass through `response_headers`

---

## 5. Configuration and Environment

### Environment Variables

- `LLM_PROXY_ENABLED` — master on/off switch for the LiteLLM proxy feature
- `LLM_PROXY_MODE` — `"internal"` or `"lite_llm"`; selects proxy vs internal routing
- `LITE_LLM_URL` — base URL of the upstream LiteLLM server
- `LLM_PROXY_TRACK_USAGE` — when `True`, streaming path buffers full response to parse token/cost from `x-litellm-response-cost` before forwarding; filtering must happen after this internal read
- `LITE_LLM_PROXY_ENDPOINTS` — JSON list of `{path, methods}` dicts; no header-filter config

### Configuration Files

- `src/codemie/configs/config.py` — master settings class (`Config`); governs all proxy/LiteLLM env vars; no header-filter config currently present
- `config/llms/llm-azure-config.yaml`, `llm-aws-config.yaml`, `llm-gcp-config.yaml`, `llm-dial-config.yaml` — provider-level model definitions; not relevant to header filtering
- `config/budgets/budgets-config.yaml` — budget definitions; not relevant

### Feature Flags and Deployment Concerns

- `LLM_PROXY_TRACK_USAGE` — the `True` path reads `x-litellm-response-cost` at `proxy_router.py:1025` before `response_headers` is built; the `False` passthrough path skips this read entirely. Both paths converge at the same `response_headers` dict at line 1371 — a single fix point covers both.
- No new env var is required for this fix — the `x-litellm-*` prefix is entirely LiteLLM-internal and should always be filtered. If per-deployment override control is desired, a `LITELLM_STRIP_RESPONSE_HEADERS` env var could be introduced, but is not required by the ticket.
- `deploy-templates/values.yaml` (Helm chart) — no changes required; header filtering is application-layer only.

---

## 6. Risk Indicators

- No existing test coverage for `x-litellm-*` response header filtering — new tests must be added to `tests/enterprise/litellm/test_proxy_router.py` alongside the implementation change
- `x-litellm-response-cost` is read in two places internally (`proxy_router.py:1025` and `llm_factory.py:69`) — confirm both reads occur before `response_headers` construction at line 1371; the code evidence confirms this ordering is already correct, but test assertions should be added to prevent regression
- `_sanitize_local_response_headers()` at `proxy_router.py:143` is used only for locally-constructed error bodies (premium budget errors, error body replacement) — LiteLLM-specific headers cannot appear in these locally-constructed responses, so no change is needed there; however, if `_handle_error_response` forwards any headers from `response_headers` (which is already filtered at line 1371), those are already covered
- The `exec()`-based dynamic function signature at `proxy_router.py:1410` (flagged with a SECURITY NOTE) is unrelated but indicates this file contains security-sensitive patterns — any changes should be reviewed carefully
- `llm-providers.md` guide documents REQUEST-side header handling but has no RESPONSE-side section — the guide should be updated to document the new filtering policy (not strictly required for the fix but prevents future confusion)
- The set of `x-litellm-*` headers emitted by LiteLLM may vary across LiteLLM versions; hardcoding an explicit set is safe for known headers but a prefix-based filter (`k.lower().startswith("x-litellm-")`) would be more robust against future LiteLLM header additions

---

## 7. Summary for Complexity Assessment

This task touches a single architectural layer — the FastAPI proxy router — and a single file: `src/codemie/enterprise/litellm/proxy_router.py`. The change surface is minimal: extend the `PROXY_RESPONSE_HOP_BY_HOP_HEADERS` set (lines 131–140) to include `x-litellm-*` header names, following the lowercase-key convention already established at line 113. The single dict comprehension at line 1371 covers all three response-return paths simultaneously (usage-tracking stream, passthrough stream, error response), so no secondary change points exist. The test file `tests/enterprise/litellm/test_proxy_router.py` requires new test cases asserting that `x-litellm-call-id`, `x-litellm-version`, `x-litellm-response-cost`, and similar headers are absent from the `headers` argument passed to `StreamingResponse` in both the `_proxy_to_llm_proxy` usage-tracking path and the passthrough path.

The task follows an established and well-documented pattern — the parallel `PROXY_HOP_BY_HOP_HEADERS` / `PROXY_RESPONSE_HOP_BY_HOP_HEADERS` structure was already designed for exactly this kind of extension. No new patterns, no new layers, no database changes, no configuration file changes, and no external service changes are introduced. The only technical decision is whether to use an explicit set of known header names or a prefix-based `startswith("x-litellm-")` check; the prefix approach is more robust against future LiteLLM version additions.

Test coverage posture is mixed: `test_proxy_router.py` has strong coverage of the proxy flow overall but has a complete gap for response-direction LiteLLM header filtering. New tests are required for the fix to be complete. The overall complexity is low — this is a one-to-two file change with well-understood precedent in the codebase, low integration surface, and no migration or deployment artifact changes.
