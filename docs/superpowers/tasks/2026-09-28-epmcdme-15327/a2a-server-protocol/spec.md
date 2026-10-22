# A2A Server Protocol v1.0 Upgrade — Server-Side Slice

**Ticket**: EPMCDME-15327, slice 2 of 4. **ACs covered**: AC2, AC4, AC5, AC7 (server side only).
**Target**: A2A spec v1.0 (negotiated value `"1.0"`), per `.ai-run/guides/integration/a2a-integration.md`.

## Context

CodeMie's in-house A2A implementation (`src/codemie/rest_api/a2a/`) speaks a pre-v1.0 draft
shape: `type`-discriminated `Part`s, old JSON-RPC method names (`tasks/send` etc.), a required
`AgentCard.url`, and a discovery path of `.well-known/agent.json`. This slice reshapes the wire
types, the router's JSON-RPC dispatch, and `CodemieTaskManager` to the confirmed v1.0 shape, and
implements real server-side streaming (`on_send_task_subscribe`/`_handle_a2a_stream` are
currently a stub and dead code respectively). Client code (`a2a/client/`), A2A-Version
negotiation/backward-compat, and the A2UI `a2a-sdk` pin are explicitly out of scope (other
slices).

## Design

### Wire types — `src/codemie/rest_api/a2a/types.py`

- Remove the `type` discriminator from `TextPart`/`FilePart`/`DataPart`; `Part` becomes a plain
  `Union` (no `Field(discriminator=...)`). Every part-consuming call site must switch to
  `isinstance` checks against the flattened fields.
- Rename JSON-RPC request classes' `method` literals to the v1.0 names and add the two new
  request/response classes needed for this slice's scope: `SendMessageRequest` (was
  `SendTaskRequest`, method `"SendMessage"`), `SendStreamingMessageRequest` (was
  `SendTaskStreamingRequest`, method `"SendStreamingMessage"`), `GetTaskRequest` (method
  `"GetTask"`), `CancelTaskRequest` (method `"CancelTask"`), and a new
  `GetExtendedAgentCardRequest`/`Response` pair (method `"GetExtendedAgentCard"`). Keep the
  existing push-notification-config and `ListTasks`/`SubscribeToTask` request classes defined
  (or add stubs for the ones that don't exist) but do not add them to the dispatch union — see
  Non-goals.
- `A2ARequest` (the discriminated `TypeAdapter` union) includes exactly the five method classes
  above.
- Rename `Task`'s bare `id` to `taskId`, add `contextId`; apply the same to
  `TaskStatusUpdateEvent`/`TaskArtifactUpdateEvent`, and give both a v1.0-shaped envelope
  (`statusUpdate`/`artifactUpdate`, no discriminator — each event type has its own shape, and
  `SendStreamingMessageResponse.result` is a plain `Union[TaskStatusUpdateEvent,
  TaskArtifactUpdateEvent]` with no `oneOf` tag). This also fixes the latent type mismatch where
  `_create_streaming_response` currently builds a full `Task` instead of one of these two event
  types.
- `AgentCard`: remove `url`; add `supportedInterfaces: list[AgentInterface]` (one entry per
  transport binding — this slice only populates the JSON-RPC entry, at the same
  `/v1/a2a/assistants/{id}` path used today) and `capabilities.extendedAgentCard: bool`. Do not
  add `signatures` (non-goal). Keep `AgentAuthentication` as-is; do not model A2A-native OAuth2
  device-code/PKCE (non-goal).
- All renamed/new fields and payloads use camelCase.

### AgentCard generation — `src/codemie/rest_api/a2a/utils.py`

- `assistant_to_agent_card` (line 215-258): replace `url=` with a single-element
  `supportedInterfaces` list carrying the same URL under the JSON-RPC transport binding; set
  `capabilities.streaming=True` (streaming is now real) and `capabilities.extendedAgentCard=True`
  (this slice implements `GetExtendedAgentCard`, returning the same card CodeMie already builds —
  there is no richer "extended" variant).
- `convert_messages_to_chat_messages` (line 92) and `_create_response_parts`
  (`codemie_task_manager.py` line 208-210): replace `part.type == "..."` / hardcoded
  `{"type": "text", ...}` dict construction with `isinstance(part, TextPart)` etc., constructing
  real `Part` model instances rather than raw dicts.

### Router — `src/codemie/rest_api/routers/a2a.py`

- Discovery route path becomes `.well-known/agent-card.json`. The existing auth/Ability split
  (lines 70-79 — skip auth for `assistant.is_global`, otherwise `authenticate` +
  `Ability(user).can(Action.READ, assistant)` → 403) is preserved byte-for-byte.
- Add a new discovery-adjacent JSON-RPC method, `GetExtendedAgentCard`, dispatched through the
  same POST endpoint and auth path as the other methods (not a separate route).
- Replace the manual `isinstance` chain (lines 123-131) with a method-name-keyed dispatch table
  (`{"SendMessage": ..., "SendStreamingMessage": ..., "GetTask": ..., "CancelTask": ...,
  "GetExtendedAgentCard": ...}`), each entry a bound `CodemieTaskManager` method. Any
  `A2ARequest`-shaped request whose method isn't in this table (`ListTasks`, `SubscribeToTask`,
  the four push-notification-config methods) returns `UnsupportedOperationError` mapped to JSON-RPC
  `MethodNotFound` — this is the explicit non-goal path, not a crash.
- For `SendStreamingMessage`, the endpoint returns a `StreamingResponse` (media type
  `text/event-stream`) that iterates the task manager's async generator and writes one
  SSE `data:` frame per yielded `SendStreamingMessageResponse`, JSON-encoded. Non-streaming
  methods keep returning a single `JSONResponse` as today.
- `_handle_exception` (line 177) and its JSONDecodeError/ValidationError/generic-Exception
  mapping carry forward unchanged.

### Task manager — `server/task_manager.py`, `server/codemie_task_manager.py`

- Add `on_cancel_task` and `on_get_extended_agent_card` to the `TaskManager` ABC alongside the
  three existing abstract methods.
- `CodemieTaskManager.on_cancel_task`: loads the `Task` by id (`Task.find_by_id`), sets its status
  to `CANCELED` if not already terminal, persists via the existing `update_store` path, and
  returns a `CancelTaskResponse` wrapping the updated `Task`. Reuses the same not-found/validation
  error handling as `on_get_task`.
- `CodemieTaskManager.on_get_extended_agent_card`: returns the same `AgentCard` the discovery
  endpoint serves (built via `assistant_to_agent_card`), wrapped in
  `GetExtendedAgentCardResponse`.
- `on_send_task_subscribe` → renamed to match the new dispatch key and made real: it calls
  `_handle_a2a_stream` (previously dead code) instead of raising
  `new_not_implemented_error`. `_handle_a2a_stream` (lines 167-206) is corrected to yield
  `TaskStatusUpdateEvent`/`TaskArtifactUpdateEvent` instances (matching the new type annotation)
  instead of a raw `Task`, but keeps its existing WORKING → COMPLETED/INPUT_REQUIRED/FAILED
  sequencing and its reliance on `agent.invoke_with_a2a_output` (still non-token-streaming from
  the LLM's perspective — only the transport is now real).
- All other helpers (`upsert_task`, `update_store`, `_append_task_history`,
  `_process_agent_response`, `_process_error_response`, `_get_user_query`, `_setup_agent`) are
  unchanged; they already operate on model objects, not raw wire dicts.

### Persistence — `a2a_tasks` table

No Alembic migration. `Task`/`TaskStatus`/`Artifact`/`Message` JSONB payloads change shape
in place; rows written under the pre-v1.0 shape will fail `PydanticType.process_result_value`'s
`model_validate` after this change ships. Documented as a known, accepted breaking change (see
Non-goals) — there is no meaningful production A2A task history predating this slice.

## Acceptance Criteria

- `GET /v1/a2a/assistants/{id}/.well-known/agent-card.json` serves an `AgentCard` with
  `supportedInterfaces` (no `url`), `capabilities.streaming=True`,
  `capabilities.extendedAgentCard=True`, and no `signatures`; the existing global/private
  auth-and-Ability split behaves identically to today.
- `POST /v1/a2a/assistants/{id}` dispatches `SendMessage`, `SendStreamingMessage`, `GetTask`,
  `CancelTask`, and `GetExtendedAgentCard` by v1.0 method name; any other well-formed method
  (`ListTasks`, `SubscribeToTask`, push-notification-config CRUD) returns a JSON-RPC
  `MethodNotFound`/`UnsupportedOperationError`, not a 500.
- `SendStreamingMessage` returns an SSE stream (`text/event-stream`) of JSON-RPC-wrapped
  `TaskStatusUpdateEvent`/`TaskArtifactUpdateEvent` frames, ending with a terminal task state.
- `CancelTask` transitions a task to `CANCELED` and persists it; calling it on an unknown task id
  returns the existing not-found error shape.
- No `Part`, `Task`, event, or JSON-RPC request/response model retains a `type`/`kind`
  discriminator or the old method-name literals; all renamed fields are camelCase
  (`taskId`, `contextId`, etc.).
- Router-level tests exist for `POST /v1/a2a/assistants/{id}` covering `SendMessage`,
  `SendStreamingMessage` (asserting SSE framing and event sequence), `GetTask`, `CancelTask`,
  `GetExtendedAgentCard`, and one non-goal method returning `MethodNotFound` — this endpoint has
  zero such tests today.
- `test_assistant_to_agent_card` and other `test_utils.py`/`test_codemie_task_manager.py`
  assertions are updated for the new field shapes; `test_on_send_task_subscribe` is rewritten to
  exercise the real streaming implementation, not a self-mock.

## Non-Goals

- A2A client changes (`A2AClient`, `A2ACardResolver`, `RemoteAgentConnections`,
  `BedrockAdapter`) — separate slice.
- `A2A-Version` header negotiation and empty-header → v0.3 backward compatibility — separate
  slice. This slice does not read or validate that header.
- Agent Card cryptographic signing (`signatures`, spec §8.4) — no key management or signature
  generation is implemented; `AgentCard.signatures` is omitted.
- A2A-native OAuth2 device-code/PKCE flows and the multi-tenant `tenant` field — the existing
  Bearer-token `authenticate`/`Ability` split is preserved as-is, not replaced.
- `ListTasks`, `SubscribeToTask`, and all four push-notification-config CRUD methods
  (`Create`/`Get`/`List`/`Delete`) — types may exist or be stubbed in `types.py` but are not
  wired into the dispatch table; calling them returns `MethodNotFound`.
- Any Alembic migration or compat-read shim for pre-existing `a2a_tasks` JSONB rows — accepted as
  a breaking change per the answered Q1 decision.
- Token-level LLM streaming — `SendStreamingMessage` streams task-lifecycle events over SSE, not
  incremental model output; `agent.invoke_with_a2a_output` remains a single non-streaming call.

## Testing

Extend, don't replace, the three existing suites (`tests/codemie/rest_api/routers/test_a2a_router.py`,
`tests/codemie/rest_api/a2a/server/test_codemie_task_manager.py`,
`tests/codemie/rest_api/a2a/test_utils.py`), following their existing patterns (httpx
`AsyncClient`/`ASGITransport` with `authenticate`/`Ability`/`Task.find_by_id` patched; no real DB
fixture). New coverage: router-level dispatch tests for all five in-scope methods plus one
non-goal method, an SSE-framing assertion for `SendStreamingMessage`, and a real (non-mocked)
`on_send_task_subscribe`/`_handle_a2a_stream` test replacing the self-mocking one.
