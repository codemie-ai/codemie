# Technical Research

**Task**: a2a client adapter protocol
**Generated**: 2026-09-30
**Research path**: filesystem

---

## 1. Original Context

EPMCDME-15327 slice 3 a2a-client-protocol: upgrade the CodeMie A2A client (src/codemie/rest_api/a2a/client/: adapter.py, client.py, card_resolver.py, remote_agent_connection.py) to protocol v1.0 per .ai-run/guides/integration/a2a-integration.md; server side is done (slice 2). Client-only scope; out of scope: server code, backward-compat with old-shape stored rows (slice 4), Alembic.
Background: target is A2A v1.0/v1.0.1 (src/codemie/rest_api/a2a/version.py). Do NOT infer target from BedrockAdapter or the installed a2a-sdk 0.3.26 (v0.3 shape, `kind` discriminator). In-house upgrade, not SDK migration. Server now uses JSON-RPC methods SendMessage/GetTask/CancelTask/GetExtendedAgentCard/SendStreamingMessage, no Part discriminator, taskId+contextId, SSE for streaming, discovery at .well-known/agent-card.json. types.py has alias SendTaskStreamingResponse = SendStreamingMessageResponse (~line 269); check which old type names still resolve.
Known stale spots to verify: adapter.py (hardcoded method="message/stream" ~line 88; kind<->type conversions ~47, 80-83); client.py (SendTaskRequest/GetTaskRequest/CancelTaskRequest/push-notification types; normalized dict in send_task_streaming ~85-104); card_resolver.py (_build_url tries agent.json and agent-card.json). Also check consumers: src/codemie/agents/assistant_agent.py, langgraph_agent.py, rest_api/handlers/assistant_handlers.py, rest_api/models/assistant.py, service/settings/settings.py.
Tests: tests/codemie/rest_api/a2a/client/{test_client,test_card_resolver,test_remote_agent_connection}.py — at end of slice 2 there were 6 failures + 23 errors there; determine causes (regression from slice-2 renames vs pre-existing; you cannot easily run on main, so reason from code/imports; tests may be run in WSL: `wsl -d Ubuntu -- bash -lc 'cd /mnt/c/Users/Artur_Kuznetsov1/codemie-dev/codemie && poetry run pytest <scoped paths> -n 0 -q -p no:cacheprovider'`, poetry at /home/art_k/.local/bin/poetry, takes 2-4 min). Also surface whether Bedrock AgentCore remotes are used via BedrockAdapter (design question: keep v0.3 adapter or not).

---

## 2. Codebase Findings

### Existing Implementations (client package, 501 lines total)
- `src/codemie/rest_api/a2a/client/adapter.py` (88): `BaseAdapter` (pass-through; `make_streaming_request` builds `SendTaskStreamingRequest(params=TaskSendParams...)`, `prepare_stream_body` -> json body) and `BedrockAdapter` (sends body as raw bytes so SigV4 can sign it; `normalize_event` reads `result.kind` in {`status-update`,`artifact-update`}, builds `TaskStatusUpdateEvent`/`TaskArtifactUpdateEvent`, `_convert_parts` renames `kind`->`type`; `make_streaming_request` copies `payload['id']` into `message.messageId` and hardcodes `method="message/stream"`).
- BUT: `SendStreamingMessageRequest.method` is `Literal["SendStreamingMessage"]` (types.py:206), so `SendTaskStreamingRequest(method="message/stream", ...)` raises pydantic ValidationError at runtime. BedrockAdapter streaming is broken on this branch (unrelated to test failures listed below since no test exercises it).
- `client.py` (172): `A2AClient(agent_card|url, user_id, project_name, integration_id)`. `self.url = url or agent_card.url` — `AgentCard.url` no longer exists (types.py AgentCard has only `supportedInterfaces: List[AgentInterface(url, transport)]`), so `from_card("url")` is always None -> POST to `None`. Adapter chosen by `agent_card.bedrock_agentcore`. Methods: `send_task`, `send_task_streaming` (sync `httpx.Client` + `httpx_sse.connect_sse` inside an async generator; `json.loads(sse.data)` -> `adapter.normalize_event` -> `SendTaskStreamingResponse(**normalized)`), `_send_request` (async httpx POST, `config.A2A_AGENT_REQUEST_TIMEOUT`), `get_task`, `cancel_task`, `set_task_callback`/`get_task_callback` (push-notification types with old method literals `tasks/pushNotification/set|get`, types.py:233/242; server returns -32600 for these). `_get_header` -> `SettingsService.get_a2a_creds` + `utils.get_auth_header`; failures logged, returns {}. No `A2A-Version` header is sent anywhere (`version.A2A_PROTOCOL_VERSION` = "1.0" unused by client).
- `card_resolver.py` (122): `build_agent_json_url` returns URL as-is if it already ends `.well-known/agent.json|agent-card.json`; else appends `.well-known/agent.json`, except URLs containing `bedrock-agentcore` which get `.well-known/agent-card.json` and set `self.bedrock_agentcore = True`. (Note: `normalize_url` strips trailing `/`, so the later `endswith('/')` add-slash branch is dead-ish; the join yields `.../.well-known/...` via that branch only when no slash - functional.) `fetch_agent_card` injects `project_name/integration_id/user_id/bedrock_agentcore` into the JSON then `AgentCard.model_validate`. Method name is `build_agent_json_url` (tests reference it).
- `remote_agent_connection.py` (119): `RemoteAgentConnections(agent_card)`; `send_task(request: TaskSendParams, task_callback)` — streaming branch builds initial `Task(id, sessionId, status SUBMITTED, history)`, iterates `send_task_streaming(request.model_dump())` and breaks on `result.final`; non-streaming uses `send_task`. `_process_error_response` builds parts as `[{"type": "text", ...}]` (dict with old `type` discriminator; Part union now has no discriminator, so dict parts are not validated as `TextPart` reliably). `merge_metadata` helper.
- `types.py` current state: `TaskSendParams` still `id`, `sessionId`, `message`, `pushNotification`; `Task.id` has validation alias `AliasChoices("taskId","id")`, serialization alias `taskId`; events `TaskStatusUpdateEvent`/`TaskArtifactUpdateEvent` same alias trick with `contextId`; `Part = Union[TextPart, FilePart, DataPart]` (no discriminator; `FileContent.mimeType`); `TaskState` still contains `SUBMITTED, WORKING, INPUT_REQUIRED, COMPLETED, CANCELED, FAILED, UNKNOWN`. `JSONRPCRequest.model_dump()` in client is called WITHOUT `by_alias=True` (client.py:87, 114) — server-facing field `taskId` would be emitted as `id` for Task objects; params classes `TaskIdParams`/`TaskQueryParams` use `id`.
- Old names still resolving (types.py:271-274, comment says "the a2a-client-protocol slice removes these"): `SendTaskRequest`, `SendTaskResponse`, `SendTaskStreamingRequest`, `SendTaskStreamingResponse`. `GetTaskRequest`, `CancelTaskRequest`, `SetTaskPushNotification*`, `GetTaskPushNotification*` were never renamed and still resolve. Consumers of the aliases: `client/adapter.py`, `client/client.py`, `rest_api/handlers/assistant_handlers.py:53` (`SendTaskRequest`, `SendTaskStreamingRequest`), and `rest_api/a2a/utils.py` already uses the v1.0 names.

### Architecture and Layers Affected
Client protocol layer (`a2a/client/*`) -> shared types (`a2a/types.py`, out of client folder but the aliases are to be removed) -> consumer handler layer (`handlers/assistant_handlers.py` `A2AAssistantHandler`, lines ~1321-1478) -> API router (`routers/a2a.py:170` `fetch_remote_assistant` uses `A2ACardResolver`; `routers/assistant.py:774` uses `agent_card.bedrock_agentcore` to set `AssistantOrigin.BEDROCK_AGENT_CORE`) -> persistence (`Assistant.agent_card` stored as `PydanticType(AgentCard)` JSONB, models/assistant.py:361, 698, 1295).

### Integration Points
- `A2AAssistantHandler.process_request` (assistant_handlers.py:1360) logs `self.agent_card.url` — AttributeError on the new `AgentCard` (pydantic has no `url`), i.e. every remote A2A call fails at this log line before reaching the client. Handler also reads `task_arg.status.message.parts` `.text`, `task_arg.artifact.parts` `.text` (works with discriminator-free parts), and `final` flag; passes `task_request.params` (`TaskSendParams`) to `send_task`.
- `agents/assistant_agent.py:404` and `agents/langgraph_agent.py:861` only define `invoke_with_a2a_output` (not client related). `service/settings/settings.py:1896` `get_a2a_creds` used by client/card resolver; no wire-shape dependency. `models/assistant.py` only carries `agent_card` and derives type A2A / description.
- Bedrock: `services/aws_bedrock/*` and `agents/aws_bedrock/agentcore_executor.py` are a separate (non-A2A) AgentCore path via boto; the A2A `BedrockAdapter` is reached only for cards flagged `bedrock_agentcore` (URL contains `bedrock-agentcore`, set by card resolver, persisted on the card, propagated through `routers/assistant.py:774`). So Bedrock AgentCore remotes are a live, user-creatable feature via `fetch_remote_assistant` (AgentCore A2A runtimes are v0.3.x: `message/stream`, `kind` discriminators, `.well-known/agent-card.json`, SigV4-signed raw body). Design question stands: keep v0.3 BedrockAdapter (fix the ValidationError, still emits `message/stream`) vs. drop it vs. dual-mode. Note AgentCore's v0.3 card carries `url` and no `supportedInterfaces`, which the required-field `AgentCard` now rejects (see risks).

### Patterns and Conventions
- Adapter strategy pattern (`BaseAdapter`/`BedrockAdapter`) selected in `A2AClient.__init__`. Pydantic v2 models; camelCase wire fields; `model_dump()` for request bodies. Guide `.ai-run/guides/integration/a2a-integration.md` calls the adapter pattern reusable but the `kind` fields non-target.

---

## 3. Documentation Findings

### Guides and Architecture Docs
- `.ai-run/guides/integration/a2a-integration.md` (read): decision + v1.0 markers (agent-card.json, no discriminator, method names, `supportedInterfaces[]`, camelCase, `A2A-Version` header; empty header means 0.3), `canceled` spelling. `a2a/version.py` docstring restates them and still says the current shapes are "pre-1.0 draft" (stale after slice 2).
- Slice 2 handover: `docs/superpowers/tasks/2026-09-29-a2a-server-protocol/HANDOVER-slice-3.md` (known gaps: SSE stream lacks the v1.0 `statusUpdate`/`artifactUpdate` envelope; accepted `-32600` for unsupported methods; no legacy-row shim). Slice 2 `technical-analysis.md`/`spec.md` exist in same dir.

### Architectural Decisions
In-house upgrade, no `a2a-sdk` (guide). Slice-3 deferred to spec gate: Bedrock v0.3 adapter fate; slice 4 owns old-shape compat.

### Derived Conventions
Docstring/license header (Apache, EPAM 2026) on every file; ruff via `make ruff`.

---

## 4. Testing Landscape

### Existing Coverage
`tests/codemie/rest_api/a2a/client/test_client.py` (524 lines), `test_card_resolver.py` (385), `test_remote_agent_connection.py` (482). All last touched in the initial commit (`afc61fa12`) — never updated by slice 2. No test for `adapter.py` / `BedrockAdapter` normalization; only `test_get_header_aws_signature` builds a bedrock-flavoured card. Handler A2A path: `tests/codemie/rest_api/handlers/test_assistant_handlers*.py` grep for `A2AAssistantHandler` finds none (test_utils covers `convert_to_task_request`).

### Testing Framework and Patterns
pytest 8.3.3, `pytest.mark.asyncio`, `unittest.mock` (`AsyncMock`, `patch` on `httpx.AsyncClient`/`SettingsService`/`get_auth_header`), fixtures constructing `AgentCard(...)`.

### Coverage Gaps / Failure causes (reasoned from code, not run)
The 29 failures/errors are almost certainly all regressions from slice 2's `AgentCard` change (`url` removed, `supportedInterfaces: List[AgentInterface]` required), not pre-existing (tests untouched since initial commit, and the old `AgentCard(url=...)` shape was valid then):
- ~23 errors: fixtures `agent_card` (test_client.py:43-52), `agent_card_streaming`/`agent_card_non_streaming` (test_remote_agent_connection.py:37-60) call `AgentCard(..., url=..., skills=[])` with no `supportedInterfaces` -> ValidationError at fixture setup.
- test_client `test_get_header_aws_signature` (line 178-190): inline `AgentCard(url=...)` fails the same way; it also relies on `client.url`.
- ~6 failures in test_card_resolver: `sample_agent_card_data` (line 33-) has `"url"` and no `supportedInterfaces`, so success-path `fetch_agent_card` tests hit "Invalid agent card format"; `build_agent_json_url` tests assert `.well-known/agent.json` (lines 124-157) — those would still PASS today but will FAIL once discovery moves to `agent-card.json`. Precise 6 count not verified; confirm with a WSL scoped run before/after.
- Tests also use `"type": "text"` in parts and `sessionId`; streaming test events use old `status-update` shapes; `test_set/get_task_callback` cover push-notification methods. These need rewriting to the v1.0 shape.

---

## 5. Configuration and Environment

### Environment Variables
`config.A2A_AGENT_REQUEST_TIMEOUT` (30.0), `A2A_AGENT_CARD_FETCH_TIMEOUT` (30.0), `HTTPS_VERIFY_SSL` (card resolver only; the SSE `httpx.Client(timeout=None)` and `AsyncClient` in `_send_request` do not pass `verify`), `A2A_PROVIDER_ORGANIZATION/URL` (server card only) — `src/codemie/configs/config.py:433-436`.

### Configuration Files
None client-specific. `pyproject.toml`: `httpx`, `httpx-sse` used; `a2a-sdk 0.3.26` transitive via `a2ui-agent-sdk` (not used by client).

### Feature Flags and Deployment Concerns
No flags. Credentials via `SettingsService.get_a2a_creds` (CredentialTypes.A2A); AWS SigV4 for Bedrock in `utils.get_auth_header` (signs `body` — bytes path used by BedrockAdapter).

---

## 6. Risk Indicators

- Any A2A remote call is currently broken on this branch: `AgentCard.url` removed -> `A2AClient.url` is None; `assistant_handlers.py:1360` raises AttributeError on `agent_card.url`. Client must pick URL from `supportedInterfaces` (JSONRPC binding) — handler must be touched too (outside the four client files).
- Stored `Assistant.agent_card` JSONB rows (old shape, `url`, no `supportedInterfaces`) fail `AgentCard` validation on load; slice 4 owns this, but slice 3 must avoid making it worse and tests must not depend on it.
- `BedrockAdapter` (v0.3): `method="message/stream"` triggers a pydantic Literal ValidationError today; v0.3 Bedrock cards are also unparseable by the required-field `AgentCard`. Design decision needed (keep/quarantine/drop); AgentCore A2A remotes are a live feature (`routers/assistant.py:774`, `AssistantOrigin.BEDROCK_AGENT_CORE`).
- `model_dump()` without `by_alias=True` in client requests emits `id`/`sessionId` rather than v1.0 `taskId`/`contextId`; `TaskSendParams` still uses `id`+`sessionId` (server accepts via aliases; need spec decision on the request-side names).
- SSE parsing: server frames currently lack the v1.0 `statusUpdate`/`artifactUpdate` envelope; the client parser must match what the server actually emits (known slice-2 gap) — spec should pin one shape. `send_task_streaming` uses blocking sync `httpx.Client` inside an async generator (pre-existing event-loop blocking).
- `A2A-Version: 1.0` header not sent; guide says missing header = 0.3 on the server side.
- `_process_error_response` builds dict parts with `type`; `connection.send_task` streaming branch returns `task=None` (pre-existing).
- Push-notification client methods target methods the server rejects with -32600; either delete or map to v1.0 `CreateTaskPushNotificationConfig`.
- Speculative: removing the `SendTask*` aliases will require updating `assistant_handlers.py` imports (outside the client folder); `version.py` docstring says "pre-1.0 draft" and may need a wording fix.
- Speculative: test rewrite is the bulk of the effort (~1,390 test lines, no adapter tests exist).

---

## 7. Summary for Complexity Assessment

Client-only surface is small (four files, ~500 LOC) sitting in the protocol layer, but it touches a live consumer (`A2AAssistantHandler` in `assistant_handlers.py`, which currently crashes on `agent_card.url`), the shared `types.py` alias block, and the card resolver route used by the assistant-creation router. Changes are mostly renames and shape alignment: v1.0 request types, `agent-card.json` discovery, URL from `supportedInterfaces`, `A2A-Version` header, dropping `kind`/`type`, alias-aware serialization, and a decision on push-notification methods.

Technical novelty is low (in-house, spec already in the guide and implemented server-side), but there is one genuine design fork: what to do with `BedrockAdapter` and v0.3 AgentCore remotes (a live feature whose cards the new `AgentCard` cannot parse and whose streaming request construction already fails). Dual-mode behaviour would blur the slice-4 boundary.

Test posture: three client test files exist, untouched since the initial commit; the 29 failures/errors are reasoned to be slice-2 `AgentCard` regressions (missing `supportedInterfaces`), not pre-existing. Adapter/BedrockAdapter and the A2A handler have no direct tests. Key risks: broken end-to-end call path, Bedrock decision, SSE envelope ambiguity, request-side alias naming.

---

## 8. External References

None named by the task (the guide `.ai-run/guides/integration/a2a-integration.md` and `src/codemie/rest_api/a2a/version.py` are in-repo and were read; `HANDOVER-slice-3.md` read for context). WSL test run was not performed; failure causes are reasoned from code.
