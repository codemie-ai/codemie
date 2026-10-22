# A2A Wire Conformance (v1.0.1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development or superpowers:executing-plans. Steps use `- [ ]`.

**Goal:** CodeMie A2A server and client exchange exact v1.0.1 JSON-RPC shapes for SendMessage, SendStreamingMessage, GetTask, CancelTask, GetExtendedAgentCard, with read-time legacy normalisation.

**Architecture:** In-house upgrade of `src/codemie/rest_api/a2a/**`, `routers/a2a.py`, `handlers/assistant_handlers.py` (A2AAssistantHandler). Legacy handling lives in nested before-validators; public-card filtering at the serving edge.

**Spec:** `docs/superpowers/tasks/2026-09-28-epmcdme-15327/a2a-wire-conformance/spec.md` (analysis: `technical-analysis.md` alongside).

Commit per task using the repository's existing convention, subject prefix `EPMCDME-15327:`. Never stage `.ai-run/sdlc-factory/doctor.json` or `.codemie/codemie-cli.config.json`.
Test run (WSL): `wsl -d Ubuntu -- bash -lc 'cd /mnt/c/Users/Artur_Kuznetsov1/codemie-dev/codemie && /home/art_k/.local/bin/poetry run pytest <paths> -n 0 -q -p no:cacheprovider'`; ruff via the same WSL poetry (see HANDOVER.md). Each task keeps its own scoped tests green; tests of later-layer modules that still use old shapes are rewritten in their own task.

## Global Constraints

- No Alembic change (c9d8e7f6a5b4 retained), no data migration, no new dependency.
- `types.py` stays import-light (must not import `a2a.utils`; `models.assistant` imports it).
- Wire names are the Python attribute names (persistence uses plain `model_dump()`, no `by_alias`/`exclude`).
- Out of scope: ListTasks, SubscribeToTask, push-config (-32601), tenant, signing, Bedrock, returnImmediately=true (-32004).

## Review Focus

- Legacy stored JSON with invalid/odd items (unknown state, `file` part, artifact `index`) must load without being dropped by PydanticListType: pinned in Task 2.
- `/assistants/fetch` must still return `project_name/integration_id/user_id/bedrock_agentcore`: Task 5.
- Non-JSON body, int `id`, missing `method`: JSON-RPC errors, not FastAPI 422: Task 5.
- Remote answers HTTP 400 with JSON-RPC error body: Task 7.
- Stream ends on `{message}` result or INPUT_REQUIRED without `final`: Task 8.

## Plan decisions (risk resolutions)

- D-a: legacy normalisers are before-validators on Message/Part/Artifact/TaskStatus (and AgentCard/AgentInterface/AgentSkill); they never raise (unknown state -> FAILED + warning; missing ids generated). `Task` itself gets no validator; `contextId` fallback is `task.contextId or task.sessionId` at read sites. The `sessionId` field is retained on the table model only as the DB column mapping, excluded from serialisation.
- D-b: no `Field(exclude=True)` on AgentCard internals. A helper `public_agent_card_dict(card)` (in `a2a/utils.py`) does `model_dump(by_alias=True, exclude_none=True, exclude={internal fields})`; used by the well-known route and GetExtendedAgentCard. `/assistants/fetch` keeps the full model.
- D-c: execute endpoint takes raw `Request`; `A2ARequestBody` stays only as Swagger `openapi_extra` doc. Order: authN -> assistant lookup/Ability -> JSON parse (-32700) -> version gate (-32009) -> envelope check (-32600) -> method lookup (-32601) -> params validation (-32602). Spec FR-S1 says gate before body validation; so parse/envelope run after the gate: gate reads header/query only, then body parse.
- D-d: client parses JSON-RPC error bodies on HTTP 4xx (and non-200 SSE) before `raise_for_status`.
- D-e: terminal-state/StreamResponse based closing in RemoteAgentConnections and handler; `{message}` result completes; `convert_to_base_model_response` accepts Task or Message.
- D-f: client sends `message.contextId=conversation_id`, never a `taskId` (server assigns; a continuation is an INPUT_REQUIRED reply where the client passes the taskId returned earlier only if it holds one; chat flow does not). `raw_request.state.uuid` is no longer used as task id.
- D-g: securitySchemes default `{"bearer": http/bearer, bearerFormat JWT}`; when `config.IDP_PROVIDER == "local"` also `{"user-id": apiKey in header "user-id"}` with matching securityRequirements. Old `authentication` and `dev-codemie-user` removed.
- D-h: history: `historyLength` None -> full, 0 -> omit (None), N -> last N.
- D-j: sync and streaming both persist SUBMITTED then WORKING before `_setup_agent`; a setup failure ends in FAILED (stream: statusUpdate FAILED frame).
- Known gap (i): `invoke_with_a2a_output` maps agent exceptions to require_user_input; unchanged.

---

### Task 1: Spec-derived fixtures

**Files:** Create `tests/codemie/rest_api/a2a/fixtures/*.json` and `fixtures/__init__.py` with `load_fixture(name)`.
**Produces:** `load_fixture(name: str) -> dict`; fixtures: agent_card, send_message_request, send_message_response_task, send_message_response_message, stream_task, stream_status_update, stream_artifact_update, get_task_response, cancel_task_response, extended_agent_card, error_* (-32700,-32600,-32601,-32602,-32001,-32002,-32004,-32005,-32009 with `data` typed array).

Test-first: no — static data derived from spec examples and `a2a.proto`; exercised by Task 2/3 round-trip tests.

- [ ] Write fixtures per proto/ProtoJSON (`TASK_STATE_*`, `ROLE_*`, Task `id`, `messageId`, `artifactId`, `supportedInterfaces[{url,protocolBinding,protocolVersion}]`, errors with `@type` ErrorInfo domain `a2a-protocol.org`); add loader. Commit.

### Task 2: Core protocol types and legacy normalisers

**Files:** Modify `src/codemie/rest_api/a2a/types.py` (TaskState, Role, Part, Message, TaskStatus, Artifact, Task, events, requests/responses, JSONRPC errors + `data`, `A2AProtocolVersionError(A2AClientError)`); Test `tests/codemie/rest_api/a2a/test_types.py`.
**Produces:** `Part` (one of text|raw|url|data, filename, mediaType); `Message`; `Artifact`; `TaskStatus`; `Task` (serialises `id`); `TaskStatusUpdateEvent`/`TaskArtifactUpdateEvent` (taskId, contextId, no `final`); `StreamResponse`, `SendMessageResponse` (exactly one variant); `SendMessageRequest`, `SendMessageConfiguration`, `GetTaskRequest`, `CancelTaskRequest`, `GetExtendedAgentCardRequest`; `JSONRPCError.data: list[dict]|None`; helper `error_info(reason, **metadata)`, `bad_request(violations)`. Remove `TaskSendParams`, `TaskIdParams` alias use.

Test-first: yes — every Task 1 fixture parses and re-serialises unchanged (TR-3); legacy per-normaliser tests (state `input-required`/`unknown`->FAILED, role `user`, `file` part, artifact with `index`, `sessionId`); `PydanticListType`-style list of legacy items loads with none dropped; Task/events dumps contain none of `final`, `kind`, `type`, `taskId` on Task.

- [ ] Write failing tests. [ ] Implement (timestamp UTC with `Z`; empty `parts` invalid but legacy path never raises). [ ] Run `tests/codemie/rest_api/a2a/test_types.py`, ruff. [ ] Commit.

### Task 3: AgentCard model and legacy card normalisation

**Files:** Modify `a2a/types.py` (AgentCard, AgentInterface, AgentSkill, capabilities, securitySchemes/securityRequirements, `get_jsonrpc_url`); Test `test_types.py`.
**Produces:** `AgentInterface{url,protocolBinding,protocolVersion,tenant?}`; internal fields kept on model; `INTERNAL_CARD_FIELDS = ("project_name","integration_id","user_id","bedrock_agentcore")`.

Test-first: yes — legacy card (`url`, `preferredTransport`, `additionalInterfaces`, interface `transport`, `authentication`, `["text"]` modes, skills lacking tags/description, top-level `protocolVersion`) validates to v1.0 shape; card fixture round-trips; `model_dump()` still contains internal fields; `get_jsonrpc_url` case-insensitive on `protocolBinding`.

- [ ] Tests, implement, run, commit.

### Task 4: Task manager

**Files:** Modify `a2a/server/task_manager.py` (signatures), `a2a/server/codemie_task_manager.py` (`on_send_task`, `on_send_streaming_message`, `on_get_task`, `on_cancel_task`, `_append_task_history`, `upsert_task`, `_check_existing_task`, `_create_streaming_response`, `_create_artifact_response`), `a2a/server/utils.py` (`text/plain`); Test `tests/codemie/rest_api/a2a/server/test_codemie_task_manager.py`.
**Consumes:** Task 2 types. **Produces:** manager methods taking `SendMessageRequest`/`GetTaskRequest`/`CancelTaskRequest`, returning `SendMessageResponse`/`StreamResponse` frames; errors as JSON-RPC responses with typed `data`.

Test-first: yes — server generates taskId/contextId (FR-S2 incl. mismatch -> -32602, foreign -> -32001, terminal -> -32004); `returnImmediately` -> -32004; disjoint `acceptedOutputModes` -> -32005; history None/0/N (D-h); streaming order `{task}`, WORKING, artifact, terminal (D-j) with no `final`; setup failure ends FAILED frame; cancel ok/terminal(-32002)/foreign/race; INPUT_REQUIRED carries `status.message`; COMPLETED carries one Artifact with `artifactId`.

- [ ] Tests, implement, run, commit.

### Task 5: Router (raw body, version gate, errors, public card)

**Files:** Modify `routers/a2a.py`, `a2a/utils.py` (add `public_agent_card_dict`), `a2a/types.py` (`A2ARequestBody` example v1.0); Test `tests/codemie/rest_api/routers/test_a2a_router.py`.
**Consumes:** Tasks 2-4.

Test-first: yes — (TR-5) header missing/`0.3`/`2.0`/`1.0`/`1.0.1`/query variants -> `-32009` with ErrorInfo `VERSION_NOT_SUPPORTED`, `supportedVersions: "1.0"`, gate only after auth and only on POST; (TR-6) ListTasks/SubscribeToTask/push*/`message/send`/garbage -> `-32601`, bad params -> `-32602` BadRequest fieldViolations, non-JSON -> `-32700`, int id / missing method -> `-32600` with 1.0 hint (all HTTP 400, none 422); SSE error before stream is a single JSON-RPC error frame; well-known card and GetExtendedAgentCard omit internal fields; `/assistants/fetch` retains them.

- [ ] Tests, implement (D-b, D-c), run, commit.

### Task 6: Card builder and converters

**Files:** Modify `a2a/utils.py` (`assistant_to_agent_card`, `tool_to_agent_skill`, `convert_to_task_request`, `convert_to_base_model_response`, `convert_messages_to_chat_messages`, remove `_get_agent_authentication`); Test `tests/codemie/rest_api/a2a/test_utils.py`.
**Produces:** `convert_to_task_request(chat_request, raw_request) -> SendMessageRequest` (D-f); `convert_to_base_model_response(result: Task | Message)`; handles empty/no-text parts without raising.

Test-first: yes — card matches fixture shape (interfaces, `capabilities{streaming:true,pushNotifications:false,extendedAgentCard:true}`, `text/plain` modes, securitySchemes per D-g with `IDP_PROVIDER` local vs other, no `authentication`, no `dev-codemie-user`); request has `message.contextId==conversation_id`, no taskId; Task and Message results convert; `raw`/`url` parts map to text placeholders by filename.

- [ ] Tests, implement, run, commit.

### Task 7: A2AClient and card resolver

**Files:** Modify `a2a/client/client.py`, `a2a/client/card_resolver.py`; Test `tests/codemie/rest_api/a2a/client/test_client.py`, `test_card_resolver.py`.
**Produces:** `send_task(payload) -> SendMessageResponse`, `send_task_streaming -> AsyncIterator[StreamResponse-bearing response]`; `A2AProtocolVersionError` raised for -32601/-32600/-32009 ("Remote agent does not support A2A protocol 1.0").

Test-first: yes — request body equals fixture (by_alias, exclude_none); `{task}` and `{message}` results parse; stream frames parse as StreamResponse, bare-frame parser gone; HTTP 400 + JSON-RPC body raises `A2AProtocolVersionError` for the three codes (D-d), other codes keep `A2AClientHTTPError`/JSON-RPC error; legacy v0.3 card resolves via FR-T11.

- [ ] Tests, implement, run, commit.

### Task 8: RemoteAgentConnections and chat handler

**Files:** Modify `a2a/client/remote_agent_connection.py`, `handlers/assistant_handlers.py` (A2AAssistantHandler ~1329-1502); Test `tests/codemie/rest_api/a2a/client/test_remote_agent_connection.py`, `tests/codemie/rest_api/handlers/test_a2a_assistant_handler.py`, `test_assistant_handlers_streaming.py`.
**Produces:** `TaskCallbackArg = Task | Message | TaskStatusUpdateEvent | TaskArtifactUpdateEvent`; terminal/INPUT_REQUIRED-based stream close (D-e); `A2AProtocolVersionError`/`A2AClientError` -> 400-class `ExtendedHTTPException`.

Test-first: yes — stream closes on terminal statusUpdate and on INPUT_REQUIRED without `final`; `{message}` result yields chat response; sync path guards `None`; version error surfaces as 400 with clear message, not 500.

- [ ] Tests, implement, run, commit.

### Task 9: Docs and version module

**Files:** Modify `.ai-run/guides/integration/a2a-integration.md`, `src/codemie/rest_api/a2a/version.py`; Test `tests/codemie/rest_api/a2a/test_version.py`.

Test-first: no — documentation; drop "supersede" wording, document verified shapes, legacy normalisation (preferredTransport, transport, part/state/role), strict version gate, `-32601` scope, `returnImmediately` limitation, remove stale "no A2A-Version handling"; adjust `test_version.py` docstring assertions if any.

- [ ] Edit and commit.

### Task 10: Rewrite remaining old-shape tests and sweep

**Files:** Modify any remaining tests found by grep (`TaskSendParams`, `sessionId`, `.final`, `FilePart`, `FileContent`, `.artifacts[0]`, `AgentAuthentication`, `stateTransitionHistory`, `transport=`) in `tests/codemie/**` and `src/**`; add contract tests asserting absence of removed keys (TR-2).

Test-first: yes — a contract test dumps card, Task, Message, both events and errors and asserts fixture key sets and that `final`, `kind`, `type`, `taskId` (on Task), `sessionId`, `authentication`, `transport`, `stateTransitionHistory` are absent.

- [ ] Grep, rewrite (never delete), run all `tests/codemie/rest_api/a2a`, `tests/codemie/rest_api/routers/test_a2a_router.py`, handler tests, ruff `check` + `format --check`. Commit.

## Post-implementation verification (owned by pipeline, not plan tasks)

- Run text-insights-agent (:10800) and howto-guide-agent (:10900) on official a2a-sdk>=1.1.5, plus legacy currency_converter (:10000) and research-agent (:10700), from `C:/Users/Artur_Kuznetsov1/codemie-dev/ai-run-demo-agents/python/agents` via Docker in WSL.
- Run the CodeMie app in WSL against Postgres (container `codemie-dev-postgres-1`); test CodeMie client -> SDK agents and SDK `test_client.py` -> a CodeMie assistant; expect a clear `A2AProtocolVersionError` for the legacy agents.
- Run Alembic `c9d8e7f6a5b4` upgrade + downgrade on real Postgres.

negative-constraints: honoured. No `Field(exclude=True)` (Task 3/5); no FastAPI body model on execute (Task 5); no `returnImmediately` background run (Task 4); no data migration/Alembic (Global); no ListTasks/Subscribe/push (Task 5); `invoke_with_a2a_output` untouched (known gap i); no quality-gate/verification tasks (Task 10 only runs scoped tests within its own rewrite).
