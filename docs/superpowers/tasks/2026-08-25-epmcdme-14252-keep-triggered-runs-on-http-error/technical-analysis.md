# Technical Research

**Task**: triggers webhook scheduler assistant httpx error handling mcp authentication
**Generated**: 2026-08-25T00:00:00Z
**Research path**: filesystem

---

## 1. Original Context

EPMCDME-14252: Ensure webhook/scheduler-triggered assistant and workflow runs are visible in UI when MCP token is expired. The core fix: (1) stop deleting triggered assistant conversations on any httpx.HTTPError - currently the actor deletes conversations on all HTTP errors, making runs invisible; (2) persist a sanitized ERROR turn for transport-level failures (connect/timeout) where /model never ran; (3) call _save_error before re-raising MCPAuthenticationRequiredException and BrokerAuthRequiredException in _ask_assistant so MCP/broker 401 errors are persisted to chat history. Files in scope: src/codemie/triggers/actors/assistant.py, src/codemie/rest_api/routers/assistant.py, tests/codemie/triggers/actors/test_actor_assistant.py, tests/codemie/rest_api/routers/test_assistant.py. Security constraint: persisted history must never contain auth headers, tokens, full URLs with query strings, or stack traces.

---

## 2. Codebase Findings

### Existing Implementations

- `src/codemie/triggers/actors/assistant.py` — Actor entry point for webhook/scheduler-triggered assistant runs. Calls `create_conversation`, then POSTs to `/v1/assistants/{id}/model` with `stream: False`. On any `httpx.HTTPError`, **deletes** the conversation via `delete_conversation` (lines 66–69). The delete is unconditional: applies to 401 (expired MCP token), 403, 408, 500, 503, connect errors, and timeouts.
- `src/codemie/triggers/actors/conversation.py` — Provides `create_conversation`, `update_conversation`, `delete_conversation`. All use `httpx.AsyncClient`. Errors are logged and swallowed. No history-write path exists here.
- `src/codemie/rest_api/routers/assistant.py` — `_ask_assistant` (lines ~2251–2346): catches `MissingContextException` (calls `_save_error`), re-raises `MCPAuthenticationRequiredException` and `BrokerAuthRequiredException` **bare** (no `_save_error`), calls `_save_error` for `ExtendedHTTPException` and catch-all. `_save_error` (lines ~2156–2175): writes `ChatHistoryData` with `ConversationStatus.ERROR` using `f"{exception.message}: {exception.details}\n{exception.help}"`.
- `src/codemie/core/exceptions.py` — Defines `ExtendedHTTPException(code, message, details, help)`, `MCPAuthenticationRequiredException(payload)`, and `BrokerAuthRequiredException(message, auth_location)` (via `TokenProviderException`).
- `src/codemie/service/security/token_providers/base_provider.py` — `BrokerAuthRequiredException` carries `auth_location`; comment explicitly states tokens must never be logged.
- `src/codemie/triggers/config.py` — `BASE_API_URL = "http://localhost:8080"` — base URL for internal actor calls; can appear in `httpx.HTTPStatusError` message strings.

### Architecture and Layers Affected

| Layer | Components |
|---|---|
| Trigger / Transport | `src/codemie/triggers/actors/assistant.py`, `src/codemie/triggers/bindings/webhook.py`, `src/codemie/triggers/bindings/cron.py` |
| REST API Orchestration | `src/codemie/rest_api/routers/assistant.py` — `_ask_assistant`, `_save_error`, `_ask_virtual_assistant` |
| Chat History Persistence | `ChatHistoryData`, `ConversationStatus.ERROR` (used by `_save_error`) |
| Exception Layer | `src/codemie/core/exceptions.py` — `MCPAuthenticationRequiredException`, `BrokerAuthRequiredException`, `ExtendedHTTPException` |

### Integration Points

- Actor calls internal REST API via `httpx.AsyncClient` at `BASE_API_URL/v1/assistants/{id}/model`.
- `/v1/assistants/{id}/model` handler is `_ask_assistant` in `assistant.py` — it owns history persistence via `_save_error`.
- `MCPAuthenticationRequiredException` is converted to HTTP 401 by `mcp_auth_required_handler` in `src/codemie/rest_api/main.py`; the HTTP contract (401 + structured payload) must be preserved for live-chat Sign in.
- `BrokerAuthRequiredException` follows a similar re-raise path.
- `delete_conversation` / `create_conversation` — actor-facing conversation management utilities.

### Patterns and Conventions

- `httpx.HTTPError` is the base class covering both `HTTPStatusError` (has `.response`) and `RequestError` (connect, timeout — no HTTP response).
- `_save_error` takes an `ExtendedHTTPException` and writes `{message}: {details}\n{help}` as the ERROR turn. All safe-to-persist values must be pre-built into an `ExtendedHTTPException` before calling `_save_error`.
- `_create_assistant_error` (in `assistant.py`) is the existing helper for building `ExtendedHTTPException` from common error situations — should be reused or extended for the 401 path.
- `MCPToolkitService._sanitize_url_for_log` and `_sanitize_exception_for_log` — existing URL/exception sanitizers used in the MCP layer; appropriate for sanitizing actor-level logs.
- Workflow `_SENSITIVE_HEADER_NAMES` — existing header denylist used in workflow nodes; reference for which headers must never be logged.

---

## 3. Documentation Findings

### Guides and Architecture Docs

- `.ai-run/guides/development/error-handling.md` — Sanitized failures pattern: log internal detail, return stable client-facing message; accurate per-failure-mode messages required.
- `.ai-run/guides/development/security-patterns.md` — Tokens and full auth headers must never be logged; relevant for sanitizing the error turn content.
- `.ai-run/guides/development/logging-patterns.md` — Safe contextual logging.

### Architectural Decisions

- `MCPAuthenticationRequiredException` currently re-raises bare to preserve HTTP 401 + payload for live-chat Sign-in. This contract must not change — `_save_error` call is inserted **before** the re-raise, not instead of it.
- The actor's `delete_conversation` call was introduced in EPMCDME-11877 as orphan cleanup. The implementation plan explicitly reverses this for all `httpx.HTTPError`s.

### Derived Conventions

- When adding a new error path to `_save_error`, build an `ExtendedHTTPException` with allowlisted fields only (status, error class, safe message). Do not pass raw `str(exc)` or any string derived from `httpx` exceptions.
- For transport-level errors (no HTTP response), the actor must persist the ERROR turn itself since `/model` never ran and `_save_error` was never called.

---

## 4. Testing Landscape

### Existing Coverage

- `tests/codemie/triggers/actors/test_actor_assistant.py` — 4 tests:
  1. Success path
  2. Trigger-source name prefix (scheduler vs webhook)
  3. `test_invoke_assistant_post_request_failure` — POST 500 → `delete_conversation` called (currently asserts the **broken** behavior; must be inverted)
  4. No delete when `created_conversation_id` is None
- `tests/codemie/rest_api/routers/test_assistant.py` — includes:
  - `test_reraises_mcp_authentication_required_without_wrapping` — asserts `mock_save_error.assert_not_called()` (asserts the **broken** behavior; must be inverted)
  - `test_virtual_assistant_reraises_mcp_authentication_required_without_wrapping` — same assertion for virtual assistant path
  - Existing `_save_error` tests for `ExtendedHTTPException` and generic errors

### Testing Framework and Patterns

- `pytest` + `pytest-asyncio` + `pytest-httpx` (`httpx_mock` fixture) + `pytest-mock` (`mocker`)
- Actor tests mock `create_conversation`, `delete_conversation`, and the `httpx_mock` interceptor for the POST call.
- Router tests use `mocker.patch` to mock `_save_error` and assert call counts + arguments.

### Coverage Gaps

- **Actor level**: No test for 401/403 (`HTTPStatusError`), connect error (`ConnectError`), or timeout (`TimeoutException`) — all gaps required by the ticket's test plan.
- **Actor level**: No test asserting that a sanitized ERROR turn is persisted on transport failure.
- **Actor level**: No leak test checking that persisted history/logs do not contain tokens, auth headers, or full URLs.
- **Router level**: No test asserting `_save_error` is called with allowlisted 401 content and that raw payload/tokens are absent.

---

## 5. Configuration and Environment

### Environment Variables

- `BASE_API_URL` (in `src/codemie/triggers/config.py`) — internal API base URL; can appear in `httpx.HTTPStatusError` string representations.

### Configuration Files

- `src/codemie/triggers/config.py` — trigger configuration including `BASE_API_URL`.

### Feature Flags and Deployment Concerns

- No feature flags for the actor or `/model` error paths.
- The `mcp_auth_required_handler` in `src/codemie/rest_api/main.py` must remain unchanged (HTTP 401 + payload shape).

---

## 6. Risk Indicators

- **`src/codemie/triggers/actors/assistant.py` lines 66–69** — `except httpx.HTTPError` block deletes conversation on ALL HTTP errors; this is the primary bug. Removing the `delete_conversation` call here is the core change.
- **Transport-layer errors have no `/model` history path** — `httpx.RequestError` (connect, timeout) means `/model` never ran; `_save_error` never fires. The actor must synthesize an ERROR turn itself using a sanitized `ExtendedHTTPException`. Requires either reusing an existing conversation history API or adding a minimal actor-side persist helper.
- **`httpx.HTTPStatusError` message includes full URL** — format is `"{status_code} {reason} for url {url}"`; the URL includes `http://localhost:8080/v1/assistants/{id}/model`. Persisting `str(exc)` verbatim would leak the internal URL. Must be sanitized to status code + error class only.
- **`MCPAuthenticationRequiredException` payload contains MCP server details** — `str(exc)` or `str(exc.payload)` must not be persisted; only allowlisted fields (`error`, `status`, `mcp_config_name`/`mcp_server_name`, `error_context`, `as_hostname`) are safe.
- **Two existing tests assert broken behavior** — `test_invoke_assistant_post_request_failure` (actor) and `test_reraises_mcp_authentication_required_without_wrapping` (router) will fail when the fix is applied and must be updated first (TDD).
- **Virtual assistant path** — `_ask_virtual_assistant` also re-raises `MCPAuthenticationRequiredException` bare; whether to also call `_save_error` there requires judgment (virtual uses `save_history=False` per existing test, so it may stay save-free).
- **No actor-facing persist helper exists** — for transport-level errors, a small helper must be added or an existing conversation history API must be used. Risk: inventing a new history format that diverges from `_save_error`'s shape.

---

## 7. Summary for Complexity Assessment

This task touches three distinct code layers: the trigger actor (`assistant.py`), the REST API router (`_ask_assistant` in `routers/assistant.py`), and the shared chat history persistence pattern (`_save_error` + `ChatHistoryData`). The file change surface is narrow — exactly 4 files in scope — but the interactions are subtle: the actor calls `/model` via HTTP and currently relies on the router's `_save_error` to persist errors, while transport-layer failures bypass the router entirely. The fix must coordinate both layers correctly.

The primary technical risk is sanitization: `httpx` exception strings embed full internal URLs, and `MCPAuthenticationRequiredException` payloads contain MCP server configuration details. Neither can appear in persisted chat history, which is user-visible. The implementation must extract only allowlisted fields (HTTP status code, stable error class name, safe user-facing message) before constructing the `ExtendedHTTPException` passed to `_save_error`. Existing sanitizer patterns (`_sanitize_url_for_log`) exist in the MCP layer and should be reused.

The testing posture starts in a constrained state: two existing tests explicitly assert the broken behavior and must be inverted as part of the TDD red→green cycle. New tests must cover 401, 5xx, connect error, and timeout paths at the actor level, plus 401 persistence at the router level, plus leak assertions verifying no tokens/URLs appear in persisted content. The test patterns (pytest-asyncio, httpx_mock, mocker) are well-established in this codebase. Overall complexity is moderate: clear scope, no schema migrations, no new external dependencies, but requires careful sanitization discipline and coordinated changes across two files.
