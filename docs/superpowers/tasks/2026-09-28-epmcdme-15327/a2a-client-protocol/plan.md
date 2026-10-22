# A2A client protocol v1.0 (slice 3) Implementation Plan

**Goal:** Upgrade the A2A client and `A2AAssistantHandler` to v1.0 per `spec.md`.
**Architecture:** In-house client in `src/codemie/rest_api/a2a/client/`; `BedrockAdapter` and push-notification methods are removed, streaming becomes async with a tolerant SSE parser.
**Conventions:** Commit per task using the repository's existing convention (prefix `EPMCDME-15327:`). Keep the Apache header on new files. Task order matters: each task leaves the client tests it touches green.

**Test command (WSL, scoped):**
`wsl -d Ubuntu -- bash -lc 'cd /mnt/c/Users/Artur_Kuznetsov1/codemie-dev/codemie && poetry run pytest <scoped paths> -n 0 -q -p no:cacheprovider'`

Negative-constraints pass: no server changes (T1 edits only shared `types.py` client-side models and aliases, not `routers/a2a.py` or server utils); no legacy-row compat (T2/T3 read `supportedInterfaces` only); no Alembic; no a2a-sdk; no Bedrock or v0.3 support (T2, T3 fail fast); no v1.0 push-config methods (T3 deletes them); `services/aws_bedrock/*` untouched; no new features (retries, pooling, auth changes).

---

### Task 1: Types, aliases and handler consumer

**Files:** `src/codemie/rest_api/a2a/types.py` (:151-167, :271-274), `src/codemie/rest_api/a2a/version.py`, `src/codemie/rest_api/handlers/assistant_handlers.py` (:53, :1360, :1367, :1468); new `tests/codemie/rest_api/handlers/test_a2a_assistant_handler.py`; `tests/codemie/rest_api/a2a/test_types.py` (extend if present, else create).

- [ ] Give `TaskIdParams.id` and `TaskSendParams.id` the `_TASK_ID_ALIAS` treatment (`taskId` on the wire, `id` accepted), and `TaskSendParams.sessionId` a serialization alias `contextId` with validation `AliasChoices("contextId", "sessionId")`. Add `populate_by_name=True`. `TaskQueryParams` inherits.
- [ ] Delete the four `SendTask*` aliases at `types.py:271-274`; switch `assistant_handlers.py` imports and annotations to `SendMessageRequest` / `SendStreamingMessageRequest`.
- [ ] In `process_request`, replace `self.agent_card.url` in the debug log with the JSONRPC interface URL via a small helper `get_jsonrpc_url(card) -> str` added to `types.py` (raises `ValueError("Agent card has no JSON-RPC interface")` when `supportedInterfaces` has no usable entry; match `transport` case-insensitively against `JSONRPC`). T3 reuses it.
- [ ] Drop the "pre-1.0 draft" wording from the `version.py` docstring.
- [ ] Test-first: yes — (a) `TaskSendParams.model_validate({"id":..,"sessionId":..}).model_dump(by_alias=True)` contains `taskId`/`contextId` and validates from either name; (b) `get_jsonrpc_url` picks the JSONRPC entry and raises on none; (c) `A2AAssistantHandler.process_request` on a card without `url` does not raise AttributeError (mock `RemoteAgentConnections`, `convert_to_task_request`, and base-class helpers).
- [ ] Run scoped tests: the new/edited test files above. Grep `src/` for `SendTask` returns nothing except `client/` files handled in T3.

### Task 2: Card resolver

**Files:** `src/codemie/rest_api/a2a/client/card_resolver.py` (:28-58, :96-118); `tests/codemie/rest_api/a2a/client/test_card_resolver.py`.

- [ ] `build_agent_json_url`: accept only a URL already ending `.well-known/agent-card.json`, else append it; drop `agent.json`. For a URL containing `bedrock-agentcore` raise `ValueError("Bedrock AgentCore (A2A v0.3) is not supported")`; `fetch_agent_card` already wraps exceptions into the `(False, None, msg)` tuple, so the error surfaces as a clear message. Remove the `bedrock_agentcore` attribute and the injection at line 112 (keep the `AgentCard.bedrock_agentcore` field and `routers/assistant.py:774` untouched).
- [ ] Send `A2A-Version: <version.A2A_PROTOCOL_VERSION>` merged into the card-fetch headers (auth headers still added when creds exist). `verify=config.HTTPS_VERIFY_SSL` is already passed; keep it.
- [ ] Test-first: yes — update fixtures to include `supportedInterfaces` and expect `agent-card.json`; add tests that the request carries `A2A-Version: 1.0` (assert on the mocked `client.get` headers) and that a `bedrock-agentcore` URL returns `(False, None, <unsupported message>)`.
- [ ] Run `tests/codemie/rest_api/a2a/client/test_card_resolver.py`.

### Task 3: A2AClient core (URL, headers, alias dumps, push removal, adapter removal)

**Files:** `src/codemie/rest_api/a2a/client/client.py` (:15-142); delete `src/codemie/rest_api/a2a/client/adapter.py`; `tests/codemie/rest_api/a2a/client/test_client.py`.

- [ ] Imports: use `SendMessageRequest/Response`, `SendStreamingMessageRequest/Response`; remove push-notification imports and `set_task_callback` / `get_task_callback`; remove the adapter import and `self.adapter`.
- [ ] `__init__`: `self.url = url or get_jsonrpc_url(agent_card)` (T1 helper); raise `ValueError` mentioning unsupported v0.3 when `agent_card.bedrock_agentcore` is true.
- [ ] `_send_request`: dump with `request.model_dump(by_alias=True, exclude_none=True)`, pass `verify=config.HTTPS_VERIFY_SSL`, and merge `{"A2A-Version": A2A_PROTOCOL_VERSION}` into `_get_header` output (put it inside `_get_header` so the error path `{}` still carries the version).
- [ ] Fix `send_task` / `get_task` / `cancel_task` to construct v1.0 request types (`GetTaskRequest`, `CancelTaskRequest` keep their names) and drop the old-name responses.
- [ ] Test-first: yes — rewrite `agent_card` fixtures with `supportedInterfaces`; new tests: URL taken from `supportedInterfaces`, `url=` override wins, no-interface card raises, bedrock card raises, posted JSON has `method="SendMessage"` and `taskId`/`contextId`, headers include `A2A-Version: 1.0`, `httpx.AsyncClient` created with `verify`. Delete push-notification and bedrock/SigV4-adapter tests (keep the `get_auth_header` unit coverage that does not depend on the adapter).
- [ ] Run `test_client.py` non-streaming tests (streaming ones are rewritten in T4).

### Task 4: Async SSE streaming with tolerant parser

**Files:** `src/codemie/rest_api/a2a/client/client.py` (`send_task_streaming`, lines 85-108); `tests/codemie/rest_api/a2a/client/test_client.py`.

- [ ] Rewrite `send_task_streaming` with `async with httpx.AsyncClient(timeout=None, verify=config.HTTPS_VERIFY_SSL)` and `aconnect_sse(...)`, `async for sse in event_source.aiter_sse()`. Body is `SendStreamingMessageRequest(params=TaskSendParams.model_validate(payload)).model_dump(by_alias=True, exclude_none=True)`; headers from `_get_header` (includes `A2A-Version`).
- [ ] Add module-level `_parse_stream_result(raw: dict) -> SendStreamingMessageResponse`: unwrap `result.statusUpdate` / `result.artifactUpdate` when present, otherwise use the bare `result`; then validate into the existing response type. Keep the existing `A2AClientJSONError` / `A2AClientHTTPError` mapping.
- [ ] Test-first: yes — `test_streaming_bare_frames` and `test_streaming_enveloped_frames` feed mocked `aiter_sse` events (status then final) and assert identical `TaskStatusUpdateEvent` results and `final`; `test_streaming_uses_async_client` asserts `aconnect_sse` and `verify` are used; a bad JSON frame raises `A2AClientJSONError`.
- [ ] Run `test_client.py`.

### Task 5: RemoteAgentConnections v1.0 shapes

**Files:** `src/codemie/rest_api/a2a/client/remote_agent_connection.py` (:58-72, :101-110); `tests/codemie/rest_api/a2a/client/test_remote_agent_connection.py`.

- [ ] Use the `TextPart` model instead of `{"type": "text"}` dicts in `_process_error_response`; pass `contextId=request.sessionId` where it builds `Task(...)`, and call `send_task_streaming(request.model_dump(by_alias=True))` / `send_task(...)` with the alias dump so payloads round-trip through `TaskSendParams.model_validate`.
- [ ] Test-first: yes — error response yields `message.parts[0]` as `TextPart` with the error text; fixtures gain `supportedInterfaces` and lose `type` on parts; streaming test uses v1.0 event shapes; dumped payload contains `taskId`.
- [ ] Run `test_remote_agent_connection.py`, then all of `tests/codemie/rest_api/a2a/client/` once. Confirm `src/` has no `BedrockAdapter`, `SendTask*`, or `set_task_callback` references (grep).
