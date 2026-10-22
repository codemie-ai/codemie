# Technical Research

**Task**: a2a server router task-manager agent-card streaming json-rpc v1.0
**Generated**: 2026-09-29
**Research path**: filesystem

---

## 1. Original Context

Slice 2 of 4 from EPMCDME-15327 (Upgrade CodeMie A2A protocol support). Prior slice 1 decided: upgrade the in-house A2A implementation (not migrate to a2a-sdk). The target protocol version is CONFIRMED: A2A spec v1.0 (v1.0.0 released 2026-03-12 with breaking changes, v1.0.1 released 2026-05-28 as a bug-fix-only patch). This is authoritative — see .ai-run/guides/integration/a2a-integration.md for the full verified decision and wire-shape markers, confirmed against https://a2a-protocol.org/latest/specification/ and https://github.com/a2aproject/A2A/releases. Read that guide FIRST as ground truth before analyzing the codebase.

Verified v1.0 wire-shape markers (from the guide — treat as authoritative, NOT to be re-derived from BedrockAdapter or the installed a2a-sdk==0.3.26, both of which are the OLDER v0.3.x shape and must not be used as the target reference):
- Discovery path /.well-known/agent-card.json (not agent.json)
- NO kind/type discriminator on the wire (v1.0.0 explicitly removed it - each binding uses its own typing)
- JSON-RPC method names: SendMessage, SendStreamingMessage, GetTask, ListTasks, CancelTask, SubscribeToTask, CreateTaskPushNotificationConfig/GetTaskPushNotificationConfig/ListTaskPushNotificationConfigs/DeleteTaskPushNotificationConfig, GetExtendedAgentCard (NOT message/send, message/stream, tasks/send, etc.)
- AgentCard.url removed, replaced by supportedInterfaces[] (one entry per transport: JSON-RPC/gRPC/REST) plus signatures (Agent Card signing) and capabilities.extendedAgentCard
- camelCase field naming (messageId, contextId, taskId, artifactId)
- Version negotiation via A2A-Version request header; negotiated value "1.0" (patch excluded); empty header interpreted as v0.3 for backward compat
- Also relevant: native multi-tenancy (tenant field), modernized OAuth2 (device code/PKCE), simplified Part (flattened FilePart/DataPart), American spelling ("canceled")
- src/codemie/rest_api/a2a/version.py already has A2A_PROTOCOL_VERSION = "1.0" confirmed and committed

Scope of THIS slice: Update AgentCard/types, the JSON-RPC dispatch in the router, and CodemieTaskManager to speak A2A v1.0, including implementing server-side streaming (currently dead code / raises UnsupportedOperationError). Preserve the discovery-endpoint auth/Ability split and the assistant execution path. Corresponds to ticket ACs: AC2 (server-side discover/call, with and without streaming), AC4 (Agent Cards accurately describe interfaces/capabilities), AC5 (auth/authz/task-results/streaming/protocol-errors, server side), AC7 (automated protocol checks, server flows).

Explicitly OUT of scope for this slice (handled by other slices): the A2A client (A2AClient, A2ACardResolver, RemoteAgentConnections), backward-compatibility/version-negotiation for previously connected agents (the A2A-Version header empty->v0.3 compat path), and the A2UI a2a-sdk dependency-pin conflict.

---

## 2. Codebase Findings

### Existing Implementations (current, pre-1.0-draft shapes — all must change)

- `src/codemie/rest_api/a2a/types.py` — all wire types. Current markers, all pre-v1.0:
  - `TextPart`/`FilePart`/`DataPart` carry `type: Literal[...]` discriminators (`Part = Annotated[Union[...], Field(discriminator="type")]`, line 72) — v1.0 removes this discriminator entirely.
  - `Message`, `TaskStatus`, `Artifact`, `Task` already use camelCase (`messageId`, `sessionId`) in most places, but `Task` has no `contextId`/`taskId` naming, uses bare `id`; `TaskStatusUpdateEvent`/`TaskArtifactUpdateEvent` use bare `id` (should map to `taskId`) and have no `kind` field to remove (none present) but are missing the v1.0 streaming envelope shape.
  - JSON-RPC request/response classes hardcode old method names as `Literal[...]` discriminators: `SendTaskRequest.method = "tasks/send"` (line 187), `SendTaskStreamingRequest.method = "tasks/sendSubscribe" | "message/stream"` (line 196), `GetTaskRequest.method = "tasks/get"` (204), `CancelTaskRequest.method = "tasks/cancel"` (214), `SetTaskPushNotificationRequest.method = "tasks/pushNotification/set"` (223), `GetTaskPushNotificationRequest.method = "tasks/pushNotification/get"` (232). None of these match the v1.0 names (`SendMessage`, `SendStreamingMessage`, `GetTask`, `CancelTask`, `CreateTaskPushNotificationConfig`, etc.) or `ListTasks`/`SubscribeToTask`/`GetExtendedAgentCard`, which do not exist yet at all.
  - `A2ARequest = TypeAdapter(Annotated[Union[SendTaskRequest, GetTaskRequest, SendTaskStreamingRequest], Field(discriminator="method")])` (line 240) is the JSON-RPC method dispatch discriminator used by the router; `CancelTaskRequest`/push-notification request classes are defined but never added to this union, so cancel/push-notification are unreachable today even under the old naming.
  - `AgentCard` (line 339): has `url: str` (required) — the field v1.0 removes — plus `authentication: AgentAuthentication` (schemes/credentials) rather than OAuth2/`capabilities.extendedAgentCard`; no `supportedInterfaces`, no `signatures`. `AgentCapabilities` (line 318) has only `streaming`, `pushNotifications`, `stateTransitionHistory` — no `extendedAgentCard`.
  - `TaskState.CANCELED = "canceled"` (line 34) already uses American spelling — no change needed there.
- `src/codemie/rest_api/routers/a2a.py` — the FastAPI router:
  - `GET /v1/a2a/assistants/{assistant_id}/.well-known/agent.json` (line 51-52) — discovery path must become `.well-known/agent-card.json`.
  - Auth/Ability split (lines 70-79): `if not assistant.is_global: user = await authenticate(...); if not Ability(user).can(Action.READ, assistant): raise ExtendedHTTPException(403, ...)`. Global assistants skip auth entirely; private/project assistants require `authenticate` + `Ability(user).can(Action.READ, assistant)`. This exact split must be preserved.
  - `POST /v1/a2a/assistants/{assistant_id}` (line 84) is the JSON-RPC execution endpoint; dispatch is a manual `isinstance` chain (lines 123-131) against `GetTaskRequest`/`SendTaskRequest`/`SendTaskStreamingRequest` — no `CancelTaskRequest` or push-notification handling wired in even though those response/request types exist in `types.py`.
  - `A2ARequestBody` (used as the FastAPI request-body model, `types.py` line 380) hardcodes example `method: "tasks/send"` and `params` shaped for old `tasks/send` — this is Swagger-facing documentation of the old shape, not just an internal type.
  - `_handle_exception` (line 177) maps `JSONDecodeError`→`JSONParseError`, `pydantic.ValidationError`→`InvalidRequestError`, everything else→`InternalError` — this error-code mapping is protocol-version-agnostic and should carry forward.
- `src/codemie/rest_api/a2a/server/task_manager.py` — `TaskManager(ABC)` with three abstract methods (`on_get_task`, `on_send_task`, `on_send_task_subscribe`); no `on_cancel_task` or push-notification methods defined at all.
- `src/codemie/rest_api/a2a/server/codemie_task_manager.py` — `CodemieTaskManager(TaskManager)`:
  - `on_send_task_subscribe` (line 80-83) is a one-line stub: `raise new_not_implemented_error(request.id)`, i.e. it raises `UnsupportedOperationError` (via `server/utils.py:new_not_implemented_error`) rather than returning it — the router's `on_send_task_subscribe` call at `routers/a2a.py:128` is not wrapped in a try/except for this specific case, so today it would propagate into the router's generic `except Exception as e: return _handle_exception(e)` (line 134-135) and get mis-mapped to a generic `InternalError`, not the intended `UnsupportedOperationError`.
  - `_handle_a2a_stream` (lines 167-206) is a fully-written, unused async generator (`AsyncIterable[SendTaskStreamingResponse]`) that: marks the task WORKING, invokes the agent non-streamingly (`agent.invoke_with_a2a_output`, no actual token-level streaming), yields one WORKING event then one final COMPLETED/INPUT_REQUIRED/FAILED event via `_create_streaming_response` (line 203-206), which wraps a full `Task` snapshot in `SendTaskStreamingResponse.result` (typed as `TaskStatusUpdateEvent | TaskArtifactUpdateEvent`, but the actual code passes a `Task`, not either event type — a latent type mismatch). Nothing in the router or `on_send_task_subscribe` calls `_handle_a2a_stream`; it is dead code today.
  - `_create_response_parts` (line 208-210) builds `[{"type": "text", "text": content}]` dicts directly — bypasses the `TextPart` model and hardcodes the `type` discriminator that v1.0 removes.
  - `upsert_task`/`update_store`/`_append_task_history`/`_process_agent_response`/`_process_error_response`/`_get_user_query`/`_setup_agent` are the persistence/agent-invocation helpers; none reference wire-shape fields that need renaming beyond what's already in `types.py` (they consume `Task`, `TaskStatus`, `Message`, `Artifact` objects, not raw dicts), so most of this logic is reusable once the underlying types and event-envelope shape change.
- `src/codemie/rest_api/a2a/server/utils.py` — `are_modalities_compatible`, `new_incompatible_types_error` (→`ContentTypeNotSupportedError`), `new_not_implemented_error` (→`UnsupportedOperationError`). Small, version-agnostic helpers.
- `src/codemie/rest_api/a2a/utils.py` (top-level, distinct from `server/utils.py`) — `assistant_to_agent_card` (line 215-258) builds the `AgentCard`: hardcodes `url=f"{scheme}://{netloc}{API_ROOT_PATH}/v1/a2a/assistants/{id}"` (line 252, the field v1.0 removes) and `version="1.0.0"` (line 253, comment "Default version" — this is the agent's *own* semantic version field, coincidentally matching the spec number; per the guide, not evidence of protocol support). `capabilities=AgentCapabilities(streaming=False, pushNotifications=False, stateTransitionHistory=True)` (line 255) — `streaming=False` is accurate today (no working stream) but would need to flip once streaming is implemented; no `extendedAgentCard` capability exists to set. `_get_agent_authentication()` (line 261) returns `AgentAuthentication(schemes=["Bearer"], credentials=...)` — the old auth-scheme shape, not v1.0 OAuth2 device-code/PKCE.
  - `convert_to_task_request` (line 44) builds `SendTaskRequest`/`SendTaskStreamingRequest` from an internal `AssistantChatRequest` — used by whatever client-side code constructs outbound task requests (out of scope for this slice per the client exclusion, but shares `types.py`).
  - `convert_messages_to_chat_messages` (line 92) switches on `part.type == "text"/"file"/"data"` (lines 111-116) — directly depends on the `type` discriminator this slice removes; must be updated to whatever typing mechanism replaces it (e.g. `isinstance` checks against flattened part fields).
- `src/codemie/rest_api/a2a/client/adapter.py` — `BedrockAdapter` (line 39) converts an external wire shape using `kind` discriminators (`status-update`/`artifact-update`, lines 47-83) and renames `kind`→`type`. **Labeled explicitly per the guide: this is the OLDER v0.3.x shape, not the v1.0 target, and must not be used as a reference for this slice's changes.** It is also client-side (out of scope) but its presence in the same package is a source of confusion the guide already flags.

### Architecture and Layers Affected

- **Router / API layer**: `src/codemie/rest_api/routers/a2a.py` — discovery endpoint path, JSON-RPC method dispatch table, streaming response handling (currently no `StreamingResponse`/SSE endpoint exists at all — `execute_a2a_request` always returns a single `JSONResponse`, even for the streaming request branch at line 128).
- **Wire-type layer**: `src/codemie/rest_api/a2a/types.py` — Pydantic models for `AgentCard`, `Task`, `Message`, `Part` union, JSON-RPC request/response envelopes, error codes.
- **Domain/task-manager layer**: `src/codemie/rest_api/a2a/server/task_manager.py` (abstract contract) and `codemie_task_manager.py` (concrete implementation) — request validation, task persistence orchestration, agent invocation, streaming generator.
- **Conversion/adapter layer**: `src/codemie/rest_api/a2a/utils.py` (`assistant_to_agent_card`, message conversions) — translates between CodeMie's internal `Assistant`/`ChatMessage` domain and A2A wire types.
- **Persistence layer**: `Task(BaseModelWithSQLSupport, table=True)` in `types.py` (line 102), backed by the `a2a_tasks` Postgres table (JSONB columns via `PydanticType`/`PydanticListType` in `src/codemie/rest_api/models/base.py`).

### Integration Points

- `assistant.toolkits` → `tools_from_toolkits_to_agent_skills` → `AgentSkill` list on the `AgentCard` (internal dependency, `models/assistant.py` → `a2a/utils.py`).
- `AssistantService.build_agent(...)` and `agent.invoke_with_a2a_output(...)` (`codemie_task_manager.py` lines 149, 182) — the assistant execution path this slice must preserve; not itself part of the A2A wire-shape change.
- `codemie.rest_api.security.authentication.authenticate` and `codemie.core.ability.Ability`/`Action` — the discovery-endpoint and execution-endpoint auth checks to preserve verbatim.
- `config.A2A_PROVIDER_ORGANIZATION` / `config.A2A_PROVIDER_URL` / `config.A2A_AGENT_CARD_FETCH_TIMEOUT` / `config.A2A_AGENT_REQUEST_TIMEOUT` (`src/codemie/configs/config.py` lines 433-436) — provider metadata and timeouts consumed by `AgentProvider` construction; timeouts are client-side (out of scope) but organization/url feed the (in-scope) `AgentCard`.
- `Task.find_by_id` / `.save()` / `.update()` (from `BaseModelWithSQLSupport`, `models/base.py` line 370) — Postgres-backed persistence via SQLAlchemy session; JSONB round-trip through `PydanticType(TaskStatus)` / `PydanticListType(Artifact)` / `PydanticListType(Message)`.

### Patterns and Conventions

- Abstract-base-class task manager (`TaskManager(ABC)`) with one concrete implementation (`CodemieTaskManager`) — adding new operations (cancel, push-notification CRUD, streaming) means extending both the ABC and the implementation.
- JSON-RPC method routing today is a discriminated-union `TypeAdapter` (`A2ARequest`) plus a manual `isinstance` chain in the router — not a registry/dispatch-table pattern; each new method requires a new `Literal`-discriminated request class, adding it to the `A2ARequest` union, and a new `isinstance` branch (or replacing this with an actual method-name dispatch dict, which is a design choice, not something already present).
- Pydantic `discriminator=` fields are the established way this codebase expresses wire-type unions (`Part`, `A2ARequest`) — v1.0's discriminator removal changes this pattern at the `Part` level specifically.
- SQLModel + custom `TypeDecorator` (`PydanticType`/`PydanticListType`) is the established pattern for storing nested Pydantic objects as JSONB — reused, not something new to invent.
- Error responses use a fixed set of `JSONRPCError` subclasses with hardcoded `code`/`message` defaults (`types.py` lines 253-310) — a convention to extend rather than replace for any new v1.0 error conditions.

---

## 3. Documentation Findings

### Guides and Architecture Docs

- `.ai-run/guides/integration/a2a-integration.md` — the authoritative, task-named source of truth for this slice (see Section 8). Confirms target version, wire-shape markers, and explicitly which prior clues (BedrockAdapter, `a2a-sdk==0.3.26`) are wrong references.
- `.ai-run/guides/api/rest-api-patterns.md` and `.ai-run/guides/api/endpoint-conventions.md` — general FastAPI router conventions for this repo (not read in full for this pass; router changes should conform to whatever response/error conventions those guides set, beyond what's A2A-specific).
- No dedicated `.ai-run/guides/agents/` doc discusses A2A specifically; A2A guidance lives solely in `integration/a2a-integration.md`.

### Architectural Decisions

- The in-house-upgrade-vs-SDK-migration decision (slice 1, `docs/superpowers/tasks/2026-09-28-a2a-version-decision/`) is already made and recorded in the guide; this slice does not revisit it.
- `src/codemie/rest_api/a2a/version.py` module docstring itself enumerates the same v1.0 wire-shape markers as the guide, and states plainly that the current implementation's `authentication` field, `type`-discriminated parts, `tasks/send`-style methods, and `.well-known/agent.json` path are the pre-1.0 draft shape to be replaced.

### Derived Conventions

- Test file naming mirrors source layout 1:1 (`server/codemie_task_manager.py` ↔ `server/test_codemie_task_manager.py`; `routers/a2a.py` ↔ `routers/test_a2a_router.py`).
- Router tests stub optional native packages (`switchyard`) via `sys.modules` injection before importing `codemie.rest_api.main` (`test_a2a_router.py` lines 20-25) — an existing local-test-environment workaround, not A2A-specific.

---

## 4. Testing Landscape

### Existing Coverage

- `tests/codemie/rest_api/routers/test_a2a_router.py` — 5 tests, all against the discovery endpoint only, all hitting the current `.well-known/agent.json` path: `test_get_agent_card_anonymous_raises_401_on_private_assistant`, `test_get_agent_card_authorized_succeeds_on_private_assistant`, `test_get_agent_card_unauthorized_raises_403_on_private_assistant`, `test_get_agent_card_anonymous_succeeds_on_global_assistant`, `test_get_agent_card_missing_raises_404`. **No test in this file exercises `POST /v1/a2a/assistants/{id}` at all** — the JSON-RPC dispatch endpoint has zero router-level test coverage today.
- `tests/codemie/rest_api/a2a/server/test_codemie_task_manager.py` — `TestCodemieTaskManager`, 22 test methods covering `on_get_task`, `on_send_task` (success + validation error), `upsert_task`, `_validate_request`, `update_store`, `_append_task_history`, `_invoke_agent`, `_setup_agent`, `_process_agent_response`, `_process_error_response`, `_get_user_query`, `_create_response_parts`. **`test_on_send_task_subscribe` (line 168) patches `on_send_task_subscribe` itself with a mock returning a canned `UnsupportedOperationError` response — it does not exercise the real one-line `raise new_not_implemented_error(...)` implementation**, so the "not implemented" behavior is documented by the test's intent but not actually verified against the real method body. `_handle_a2a_stream` has no test coverage at all (dead code, untested).
- `tests/codemie/rest_api/a2a/test_utils.py` — `TestA2AUtils(unittest.TestCase)`, 9 tests covering `convert_to_task_request` (streaming + non-streaming), `convert_to_base_model_response`, `convert_messages_to_chat_messages`, `to_kebab_case`, `tool_to_agent_skill`, `tools_from_toolkits_to_agent_skills`, `assistant_to_agent_card`, `get_auth_header`. `test_assistant_to_agent_card` (line 253) will need its assertions updated for any `AgentCard` field renames (`url` removal, `supportedInterfaces` addition).
- `tests/codemie/rest_api/a2a/test_version.py` — 2 tests confirming `A2A_PROTOCOL_VERSION == "1.0"` and that `version.py` has no imports (from the prior slice; already committed, out of this slice's job to change, but the constant it protects is exactly what a version-negotiation implementation in this slice or the backward-compat slice would consume).
- `tests/codemie/rest_api/a2a/client/*` — `test_card_resolver.py`, `test_client.py`, `test_remote_agent_connection.py` cover the A2A **client**, explicitly out of scope for this slice, but they import from the same `types.py`, so any wire-shape rename in `types.py` is a shared blast radius even though the client's own logic isn't this slice's job.

### Testing Framework and Patterns

- `pytest` with `pytest.mark.asyncio` for async task-manager/router tests; `unittest.TestCase`-style class for `test_utils.py` (mixed conventions within the same package).
- Mocking via `unittest.mock.patch`/`patch.object`/`AsyncMock`, applied directly to `Task.find_by_id`/`.save`/`.update` class methods rather than a database fixture or fake repository — no real Postgres/session fixture is used for task-manager tests.
- Router tests use `httpx.AsyncClient` with `ASGITransport` against the real `codemie.rest_api.main.app`, with `authenticate`/`Ability`/`Assistant.find_by_id` patched per-test (see `mock_find_by_id` used at `test_a2a_router.py` line 187) rather than a full auth stack.

### Coverage Gaps

- No router-level test for the JSON-RPC `POST` endpoint at all today (send/get/cancel/stream) — this slice's dispatch-table rewrite and streaming implementation start from zero router coverage, not a regression baseline, for that endpoint.
- No test exercises the real (non-mocked) `on_send_task_subscribe`/`_handle_a2a_stream` behavior — streaming is both unimplemented and untested; implementing it is genuinely new test surface, not a rename of existing assertions.
- No test constructs or validates a `CancelTaskRequest`/`SetTaskPushNotificationRequest` flow end to end (these request types are unused by the router's `isinstance` dispatch today).
- No test asserts on the discovery-endpoint response body's field shape (e.g., no assertion checks `resp.json()["url"]` or similar) beyond status codes in the 5 router tests read — so AgentCard field renames are lower-risk to the *existing* discovery tests than the field list in Section 2 might suggest, but AC4 ("Agent Cards accurately describe interfaces/capabilities") will need new assertions written from scratch.

---

## 5. Configuration and Environment

### Environment Variables

- No `A2A_*` values are read directly via `os.environ`/`getenv` in the A2A package; all A2A settings are typed `Settings` fields in `src/codemie/configs/config.py`: `A2A_AGENT_CARD_FETCH_TIMEOUT: float = 30.0`, `A2A_AGENT_REQUEST_TIMEOUT: float = 30.0` (both client-side, out of scope), `A2A_PROVIDER_ORGANIZATION: str = ""`, `A2A_PROVIDER_URL: str = ""` (both consumed by `assistant_to_agent_card`, in scope).

### Configuration Files

- `src/codemie/configs/config.py` (lines 433-436) — the only config surface specific to A2A; no separate `a2a.yml`/`.env.example` entries found beyond whatever standard env-var loading `config.py` performs for all `Settings` fields.

### Feature Flags and Deployment Concerns

- No feature flag or runtime toggle gates the A2A router or task manager — the endpoints are always registered, and `AgentCapabilities.streaming` is a static `False` in `assistant_to_agent_card` rather than a config-driven flag. Any decision to gate the new streaming implementation behind a flag would be new, not an existing pattern to reuse.
- No Dockerfile/CI/CD reference to A2A specifically was found; this is a plain in-process FastAPI router with a Postgres-backed table, deployed as part of the normal application image.

---

## 6. Risk Indicators

- Speculative: the JSON-RPC request/response classes and the `A2ARequest` discriminated union in `types.py` will need near-total renaming (method literals, `AgentCard` fields, `Part` discriminator removal) — this is the highest-blast-radius file in the slice because both the router and `a2a/utils.py` import directly from it, and the client package (out of scope) also imports from the same module, so a rename here risks breaking client code this slice is not meant to touch.
- Speculative: implementing real streaming requires deciding the transport (SSE via `StreamingResponse`/`EventSourceResponse` vs. chunked JSON-RPC) since none exists today — `_handle_a2a_stream` yields Python objects from an async generator but the router has no code path that consumes an `AsyncIterable` and turns it into an HTTP streaming response; `execute_a2a_request` always does `JSONResponse(response.model_dump(...))` on a single object.
- The `SendTaskStreamingResponse.result: TaskStatusUpdateEvent | TaskArtifactUpdateEvent | None` type annotation does not match what `_create_streaming_response` actually constructs (a full `Task`, not either event type) — this latent bug carries into any v1.0 rework and should be resolved rather than propagated into the new event-envelope shape (statusUpdate/artifactUpdate oneOf per the guide).
- Zero router-level test coverage exists for the JSON-RPC POST endpoint (send/get/cancel/stream) today — AC7 ("automated protocol checks, server flows") has no regression baseline to build from for this endpoint; only the discovery endpoint (5 tests) and the task-manager unit tests (22 tests, several via mocked/patched methods) exist.
- `test_on_send_task_subscribe` in `test_codemie_task_manager.py` mocks out the method under test, so its "passing" status today gives no real confidence about the current or future streaming behavior — a plan should not treat it as a safety net.
- The persisted `Task` SQLModel (`a2a_tasks` table, JSONB `status`/`artifacts`/`history` columns) has one migration (`8f68ee6d23b6_create_a2a_tasks.py`) and no later A2A-shape migration; JSONB columns are schema-less at the DB level (no column-level constraint on internal field names), so a v1.0 field rename inside `TaskStatus`/`Artifact`/`Message` does not require an Alembic migration, but it does create a read-compat concern for any row persisted under the old shape (`type`-discriminated parts, non-`kind`-free events) if the deployment has existing data — no compat-read path exists in `PydanticType.process_result_value` today (it does a direct `model_validate(value)`, which would fail hard on old-shaped JSON if fields are renamed rather than only added).
- `a2a/utils.py`'s `convert_messages_to_chat_messages` switches on `part.type` (a string comparison against the discriminator being removed) — every `Part`-consuming function in this file and in `codemie_task_manager.py::_create_response_parts` needs to move off `type`-based branching consistently, or the two will silently disagree on shape.
- `BedrockAdapter` (`client/adapter.py`) and the transitively-installed `a2a-sdk==0.3.26` remain in the codebase as `kind`-shaped v0.3.x reference material; because they sit in the same package tree as this slice's target files, there's a real risk of a future contributor (or an LLM without the guide loaded) copying their shape by proximity — the guide already flags this, but the risk persists at the code level until the client slice addresses `BedrockAdapter` directly.
- AgentCard OAuth2/multi-tenancy/`Part`-flattening markers (device-code/PKCE, `tenant` field, flattened `FilePart`/`DataPart`) are named in the guide as "new in v1.0.0" but are not analyzed in depth here since they sit partly outside this slice's explicit AC list (AC2/AC4/AC5/AC7 focus on discovery, dispatch, task manager, streaming) — flagged as an open question for the plan/spec writer to scope explicitly in vs. out for this slice.

---

## 7. Summary for Complexity Assessment

This slice touches four tightly-coupled layers of a ~1,500-line in-house A2A implementation: the wire-type layer (`src/codemie/rest_api/a2a/types.py` — AgentCard, Task/Message/Part models, the full JSON-RPC request/response/error hierarchy and its method-name discriminators), the router layer (`src/codemie/rest_api/routers/a2a.py` — discovery path, JSON-RPC dispatch, and a from-scratch streaming HTTP response), the task-manager layer (`src/codemie/rest_api/a2a/server/codemie_task_manager.py` and its ABC), and the AgentCard-generation conversion layer (`src/codemie/rest_api/a2a/utils.py`). None of these files can change in isolation: `Part`'s discriminator removal cascades into every function that currently branches on `part.type` (in `a2a/utils.py` and `codemie_task_manager.py`), and every JSON-RPC method-name rename cascades from `types.py` through the router's `isinstance` dispatch and the `A2ARequest` union.

Technical novelty is concentrated in streaming: `on_send_task_subscribe` is a one-line stub today, and its sibling `_handle_a2a_stream` is a complete but unused and untested async generator whose result type already mismatches its declared type hint. Wiring this up as a real v1.0-shaped SSE/streaming response requires both a new HTTP transport mechanism (nothing in the router today turns an `AsyncIterable` into a streaming HTTP response) and a new event envelope (statusUpdate/artifactUpdate `oneOf`, no discriminator) — this is new capability, not a reshape of working code, and it is one of only two areas (the other being cancel/push-notification CRUD) where request/response classes exist in `types.py` but are never reachable from the router today.

Test-coverage posture is uneven: the discovery endpoint has 5 passing tests (status-code-level only, no field-shape assertions) that should catch most auth/routing regressions once the path renames to `agent-card.json`; the task manager has 22 unit tests but its streaming test is a self-mocking tautology; and the JSON-RPC POST endpoint — the core of this slice's dispatch-table work — has zero router-level tests today. Key risks: JSONB-persisted `Task` rows have no compat-read path for a field-shape change (direct `model_validate`, no migration expected or present), the client package and its tests import the same `types.py` this slice must reshape despite being explicitly out of scope, and `BedrockAdapter`/`a2a-sdk==0.3.26` sit in the same package tree as visible-but-wrong v0.3.x shape references that the guide has already had to correct once.

---

## 8. External References

- `.ai-run/guides/integration/a2a-integration.md` — resolved, read in full. This is the task's named authoritative source. Key facts already carried into Sections 1, 2, 3, and 6 above:
  - Decision: upgrade in-house `src/codemie/rest_api/a2a/`, not migrate to `a2a-sdk`.
  - Target: A2A spec v1.0 (v1.0.0 breaking changes 2026-03-12; v1.0.1 bug-fix-only patch 2026-05-28); negotiated value `"1.0"` (patch excluded, per spec §3.6).
  - Correction on record in the guide itself (dated 2026-09-29): an earlier version of the guide wrongly named `kind` discriminators as the target, inferred from `BedrockAdapter` and the transitively-installed `a2a-sdk==0.3.26` — both confirmed to be the older v0.3.x shape, not the v1.0 target.
  - Verified v1.0 markers: discovery path `/.well-known/agent-card.json`; no `kind`/`type` discriminator on the wire; JSON-RPC method names `SendMessage`/`SendStreamingMessage`/`GetTask`/`ListTasks`/`CancelTask`/`SubscribeToTask`/push-notification-config CRUD/`GetExtendedAgentCard`; `AgentCard.url` removed in favor of `supportedInterfaces[]` + `signatures` + `capabilities.extendedAgentCard`; camelCase field naming; `A2A-Version` header negotiation (empty header → v0.3 compat, handled by a different slice); native multi-tenancy (`tenant` field), modernized OAuth2 (device code/PKCE), flattened `Part` (no separate `FilePart`/`DataPart` discriminator), American spelling (`canceled`).
  - Explicitly named "Consuming Slices": `a2a-server-protocol` (this slice), `a2a-client-protocol`, `a2a-backward-compat`.
  - Evidence cited by the guide itself: `src/codemie/rest_api/a2a/client/adapter.py` (`BedrockAdapter`, v0.3.x reference, not target); `src/codemie/rest_api/a2a/utils.py` (hardcoded `version="1.0.0"` in `assistant_to_agent_card`, clarified as the agent's own semantic version, not protocol-version evidence); `pyproject.toml`/`poetry.lock` (transitive `a2a-sdk 0.3.26` pin via `a2ui-agent-sdk 0.5.0`, unrelated A2UI feature, out of scope for this slice).
