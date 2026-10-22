# Technical Research

**Task**: a2a protocol agent-card sdk streaming
**Generated**: 2026-09-28T00:00:00Z
**Research path**: filesystem

---

## 1. Original Context

Ticket: EPMCDME-15327 — Upgrade CodeMie A2A protocol support

Description:
Update CodeMie's A2A client and server to the latest published protocol version. Choose the implementation approach that is fastest to deliver and practical to maintain:
- Upgrade the existing in-house implementation; or
- Migrate the protocol layer to the official A2A SDK.

Evaluate both options briefly before implementation. The SDK option must account for the current A2UI dependency on an older `a2a-sdk` version. Preserve CodeMie-specific access control, credentials, assistant execution, and chat history.

Acceptance Criteria:
1. The target A2A version and the chosen implementation approach are documented, with a short rationale covering effort, maintenance, and the A2UI dependency.
2. CodeMie can discover and call a remote agent using the target protocol version, with and without streaming where advertised.
3. An external A2A client can discover and call a CodeMie assistant using the target protocol version.
4. Agent Cards accurately describe the interfaces and capabilities that CodeMie supports.
5. Authentication, authorization, task results, streaming completion, and protocol errors work in both applicable directions.
6. The behavior of previously connected agents is defined and verified: they either remain usable through a supported compatibility path or receive a clear incompatibility error.
7. Automated protocol checks cover the supported client and server flows.

---

## 2. Codebase Findings

### Existing Implementations

CodeMie's A2A layer is a **fully in-house implementation** — it does not import the published `a2a` / `a2a-sdk` Python package anywhere. All JSON-RPC types, `AgentCard`, `Task`, `Message`/`Part` models, and client/server plumbing are hand-written Pydantic/SQLModel classes under `src/codemie/rest_api/a2a/`:

- `src/codemie/rest_api/a2a/types.py` (397 lines) — in-house JSON-RPC and data models: `AgentCard` (fields: `name`, `description`, `url`, `version`, `provider`, `capabilities`, `authentication`, `skills`, `defaultInputModes`/`defaultOutputModes`), `AgentCapabilities` (`streaming`, `pushNotifications`, `stateTransitionHistory`), `AgentAuthentication` (`schemes`, `credentials`), `Task` (SQLModel table `a2a_tasks`), `TaskStatus`, `Message`/`TextPart`/`FilePart`/`DataPart` (discriminated on `type`, not `kind`), `SendTaskRequest`/`SendTaskResponse`, `SendTaskStreamingRequest`/`SendTaskStreamingResponse`, `GetTaskRequest`, `CancelTaskRequest`, push-notification config types, and JSON-RPC error types (`JSONParseError`, `InvalidRequestError`, `InternalError`, `TaskNotFoundError`, `ContentTypeNotSupportedError`, `UnsupportedOperationError`).
- `src/codemie/rest_api/a2a/utils.py` — `assistant_to_agent_card()` builds the CodeMie Agent Card (hardcodes `version="1.0.0"`, `capabilities=AgentCapabilities(streaming=False, ...)`); `tool_to_agent_skill()`/`tools_from_toolkits_to_agent_skills()` map assistant tools to `AgentSkill`s; `get_auth_header()` + `_basic_auth`/`_apikey_auth`/`_aws_signature_auth`/`_bearer_auth` build outbound auth headers from stored integration credentials; `convert_to_task_request()` and `convert_messages_to_chat_messages()` bridge A2A `Message`/`Task` to CodeMie's `AssistantChatRequest`/`ChatMessage`.
- `src/codemie/rest_api/a2a/client/client.py` — `A2AClient`: `send_task()` (plain JSON-RPC POST), `send_task_streaming()` (SSE via `httpx_sse.connect_sse`), `get_task()`, `cancel_task()`, push-notification setters/getters. Auth header sourced per-call via `SettingsService.get_a2a_creds()`.
- `src/codemie/rest_api/a2a/client/remote_agent_connection.py` — `RemoteAgentConnections.send_task()`: branches on `card.capabilities.streaming` to pick streaming vs. non-streaming path; drives `task_callback`.
- `src/codemie/rest_api/a2a/client/card_resolver.py` — `A2ACardResolver.fetch_agent_card()`: fetches `.well-known/agent.json` (or `.well-known/agent-card.json` when the URL contains `bedrock-agentcore`), attaches credentials via `get_auth_header`.
- `src/codemie/rest_api/a2a/client/adapter.py` — `BaseAdapter` vs `BedrockAdapter`: the Bedrock adapter already normalizes a **newer-spec-shaped** event (`kind: status-update|artifact-update`, `part["kind"]` instead of `part["type"]`, method `"message/stream"` instead of the in-house `tasks/sendSubscribe`-style request) back into CodeMie's legacy in-house types. This is a working precedent for spec-version translation inside the existing codebase.
- `src/codemie/rest_api/a2a/server/task_manager.py` — abstract `TaskManager` (`on_get_task`, `on_send_task`, `on_send_task_subscribe`).
- `src/codemie/rest_api/a2a/server/codemie_task_manager.py` — `CodemieTaskManager(TaskManager)`: `on_send_task()` invokes `AssistantService.build_agent(...).invoke_with_a2a_output(...)`; `on_send_task_subscribe()` **raises `new_not_implemented_error`** — i.e. despite a private `_handle_a2a_stream()` generator existing in the same file, the server does not currently serve streaming responses at all. `AgentCard.capabilities.streaming` is hardcoded `False`, consistent with that gap.
- `src/codemie/rest_api/a2a/server/utils.py` — `are_modalities_compatible()`, `new_incompatible_types_error()`, `new_not_implemented_error()`.
- `src/codemie/rest_api/routers/a2a.py` — FastAPI router `prefix="/v1/a2a"`: `GET /assistants/{id}/.well-known/agent.json` (auth bypass only for `assistant.is_global`; otherwise `authenticate` + `Ability(user).can(Action.READ, assistant)`); `POST /assistants/{id}` (`execute_a2a_request`, always authenticated, dispatches on JSON-RPC method to `on_get_task`/`on_send_task`/`on_send_task_subscribe`); `GET /assistants/fetch` (`fetch_remote_assistant`, authenticated, used to register a remote agent as an integration).
- `src/external/alembic/versions/8f68ee6d23b6_create_a2a_tasks.py` and `074efb996aac_update_assistants_for_a2a.py` — schema for the `a2a_tasks` table and assistant columns supporting A2A.

### Architecture and Layers Affected

- **API/Router layer**: `src/codemie/rest_api/routers/a2a.py` — discovery endpoint, JSON-RPC execution endpoint, remote-fetch endpoint.
- **Protocol/domain layer**: `src/codemie/rest_api/a2a/` (types, utils, client/*, server/*) — this is where an in-house-upgrade or SDK-migration would land.
- **Service layer**: `AssistantService.build_agent(...)` / `agent.invoke_with_a2a_output(...)` (`src/codemie/service/assistant_service.py`, `src/codemie/agents/assistant_agent.py`) — assistant execution entry point the task manager calls into; must be preserved regardless of protocol layer choice.
- **Security layer**: `codemie.rest_api.security.authentication.authenticate`, `codemie.core.ability.Ability`/`Action` — access control gate in front of both discovery and execution endpoints.
- **Settings/credentials layer**: `SettingsService.get_a2a_creds()` (`src/codemie/service/settings/settings.py`) — resolves stored per-user/project/integration credentials for outbound A2A calls.
- **Persistence layer**: `Task` SQLModel (`a2a_tasks` table) — task state storage, referenced by `Task.find_by_id`, `.save()`, `.update()`.
- **Adjacent integration**: AWS Bedrock AgentCore (`src/codemie/service/aws_bedrock/agentcore/*`, `src/codemie/agents/aws_bedrock/agentcore_executor.py`) — a separate remote-agent integration that already speaks newer-spec-shaped events; the `BedrockAdapter` in the A2A client is the seam between it and CodeMie's in-house types.

### Integration Points

- Remote agent discovery/consumption: `fetch_remote_assistant` router endpoint → `A2ACardResolver` → `RemoteAgentConnections`/`A2AClient` → stores the fetched `AgentCard` on an integration/assistant record (consumed again on send).
- Outbound auth: `get_auth_header()` supports Bearer, Basic, API-key header, and AWS SigV4 (via `botocore`) — used identically by the client (`A2AClient._get_header`) and card resolver.
- Inbound auth: `codemie.rest_api.security.authentication.authenticate` + `codemie.core.ability.Ability` — same primitives used elsewhere in the REST API, not A2A-specific.
- **A2UI dependency**: `a2ui-agent-sdk = "0.5.0"` (`pyproject.toml`) declares `a2a-sdk = ">=0.3.0,<0.4.0"` as one of its own dependencies (`poetry.lock`, confirmed: `a2a-sdk` resolved at `0.3.26`). CodeMie's own A2A code does **not** import `a2a-sdk` at all today — the pin only exists transitively through A2UI. A2UI itself (`src/codemie/core/a2ui/config.py`) is a separate protocol (agent-authored UI surfaces, wire version `v0.9.1`) unrelated to Agent2Agent; it does not use the A2A wire protocol directly, so the two features share only the dependency graph, not code.
- Third-party packages used by the in-house A2A layer: `httpx`, `httpx_sse` (SSE streaming), `botocore` (SigV4), `pydantic`/`sqlmodel` — no A2A-specific SDK.

### Patterns and Conventions

- Router → `Ability`/`authenticate` gate → domain object lookup (`Assistant.find_by_id`) → service call — same pattern as other REST routers.
- Adapter pattern (`BaseAdapter`/`BedrockAdapter`) already used to translate between the in-house wire shape and a newer-spec-shaped remote agent (Bedrock AgentCore) — a directly reusable pattern for a version-compatibility shim.
- Abstract base class (`TaskManager`) for server-side dispatch, subclassed once (`CodemieTaskManager`).
- JSON-RPC error responses built via small factory functions (`new_incompatible_types_error`, `new_not_implemented_error`) rather than exceptions crossing the router boundary — `_handle_exception` in the router does the same for parse/validation errors.

---

## 3. Documentation Findings

### Guides and Architecture Docs

- No guide under `.ai-run/guides/` is dedicated to A2A specifically; `.ai-run/guides/integration/external-services.md` and `.ai-run/guides/api/rest-api-patterns.md` cover general integration/router conventions applicable here.
- `docs/superpowers/tasks/2026-09-14-a2a-agent-card-auth/technical-analysis.md` — prior task on the same router; documents the now-fixed public agent-card exposure and confirms the auth pattern (`authenticate` + `Ability(user).can(Action.READ, assistant)`, with `assistant.is_global` as the intentional bypass).
- `docs/superpowers/runs/20260804-1149-interactive-protocol-research/a2ui-deep-dive.md` — deep dive on **A2UI** (not A2A); documents `a2ui-agent-sdk` 0.5.0, its transitive `a2a-sdk` pin, and that A2A 1.0 has an official A2UI extension (envelopes carried in a `DataPart`) — relevant context if the A2A upgrade later needs to interoperate with A2UI-over-A2A, but out of this ticket's stated scope.
- `src/codemie/core/a2ui/config.py` docstring is a good example of the "single place a version is named" convention this repo favors; a comparable module for A2A version constants does not currently exist (version is hardcoded inline, e.g. `version="1.0.0"` in `assistant_to_agent_card`).

### Architectural Decisions

- No ADR file found for A2A. The only recorded decision is the auth-gap fix in the 2026-09-14 task (intentional-public exemption for global assistants).

### Derived Conventions

- Version/capability values are currently hardcoded literals inside `assistant_to_agent_card` (`version="1.0.0"`, `streaming=False`) rather than centralized constants — unlike A2UI's `config.py` pattern of a single named-version module.
- The `authentication` field on `AgentCard` and method names like `tasks/send` (implied by `SendTaskRequest`) match a **pre-1.0 draft of the A2A spec** (task-centric JSON-RPC, `type` discriminator on parts). The `BedrockAdapter.normalize_event`/`make_streaming_request` code shows the newer spec uses `kind` discriminators, `message/stream` method, and `status-update`/`artifact-update` event kinds — evidence of the gap between CodeMie's in-house types and the target ("latest published") protocol version.

---

## 4. Testing Landscape

### Existing Coverage

Substantial existing test coverage for the in-house A2A stack (~2,425 lines total):

- `tests/codemie/rest_api/routers/test_a2a_router.py` (196 lines) — discovery endpoint auth/permission matrix (anonymous vs. authenticated, global vs. private assistant), execute endpoint.
- `tests/codemie/rest_api/a2a/client/test_client.py` (524 lines) — `A2AClient` send/stream/get/cancel/push-notification methods, error mapping.
- `tests/codemie/rest_api/a2a/client/test_card_resolver.py` (385 lines) — URL normalization, `.well-known/agent.json` vs. `agent-card.json` (Bedrock) branching, credential attachment, error paths.
- `tests/codemie/rest_api/a2a/client/test_remote_agent_connection.py` (482 lines) — streaming vs. non-streaming branch, callback/metadata merge behavior, error task construction.
- `tests/codemie/rest_api/a2a/server/test_codemie_task_manager.py` (466 lines) — `on_send_task`, `on_get_task`, task upsert/update, `_invoke_agent`, error handling; presumably covers (or should be checked for) the `on_send_task_subscribe` not-implemented path.
- `tests/codemie/rest_api/a2a/test_utils.py` (372 lines) — `assistant_to_agent_card`, skill mapping, `get_auth_header` for each auth type.

### Testing Framework and Patterns

- `pytest` with `pytest-asyncio`/`anyio` fixtures (`anyio_backend` fixture seen in the router test); `httpx.AsyncClient` + `ASGITransport` against the real FastAPI `app` for router-level tests; `unittest.mock.patch`/`MagicMock` for service and settings mocking; a module-load-time stub for the `switchyard` native package to keep Windows-CI test collection working.

### Coverage Gaps

- No test exercises an actual **streaming server response** end-to-end (the server path is a stub that raises `UnsupportedOperationError`), so there is no regression net for turning streaming on server-side.
- No test targets `a2a-sdk`/official-SDK types directly (none are imported), so a migration to the SDK starts with zero adapted test coverage for SDK-native models.
- No cross-version compatibility test (old agent card shape vs. new) exists yet — relevant to AC6 (behavior of previously connected agents).

---

## 5. Configuration and Environment

### Environment Variables

- `A2A_AGENT_CARD_FETCH_TIMEOUT` (float, default 30.0) — timeout for fetching a remote `.well-known/agent.json`.
- `A2A_AGENT_REQUEST_TIMEOUT` (float, default 30.0) — timeout for JSON-RPC task requests.
- `A2A_PROVIDER_ORGANIZATION`, `A2A_PROVIDER_URL` (str, default `""`) — populate `AgentCard.provider`.
- `HTTPS_VERIFY_SSL` — used by `A2ACardResolver` for outbound TLS verification.

### Configuration Files

- `src/codemie/configs/config.py` — the four `A2A_*` settings above, defined alongside all other backend config (no dedicated A2A config module).
- `pyproject.toml` / `poetry.lock` — dependency pins: `a2ui-agent-sdk = "0.5.0"` (direct); `a2a-sdk` resolved to `0.3.26` (transitive, constrained by A2UI to `>=0.3.0,<0.4.0`); no direct `a2a-sdk` dependency declared by CodeMie itself today.

### Feature Flags and Deployment Concerns

- No feature flag gates A2A behavior; the only runtime branch is `assistant.is_global` (public vs. private discovery) and `card.capabilities.streaming` (client-side streaming choice, always `False` for CodeMie-originated cards since the server never sets it `True`).
- No Dockerfile/CI reference to A2A specifically was found.

---

## 6. Risk Indicators

- **Protocol-version gap is real and multi-surfaced**: CodeMie's `AgentCard`/JSON-RPC shapes (`authentication`, `type`-discriminated parts, `tasks/send`-style requests) match a pre-1.0 A2A draft. The `BedrockAdapter` already proves the target/newer spec uses different field names (`kind`, `message/stream`, `status-update`/`artifact-update`) — any upgrade touches a public wire contract that external and internal clients depend on (AC2, AC3, AC6).
- **Server-side streaming is not implemented today** (`on_send_task_subscribe` raises `UnsupportedOperationError`, `capabilities.streaming=False`), while a dead `_handle_a2a_stream` generator already exists unused in `codemie_task_manager.py`. Turning on streaming per AC2/AC5 is new server behavior, not a straightforward version bump, and has no existing regression test.
- **A2UI's transitive `a2a-sdk` pin (`>=0.3.0,<0.4.0`, resolved 0.3.26) is a real constraint** if the SDK-migration path is chosen: CodeMie's own code imports no SDK types today, so adopting `a2a-sdk` directly for the in-house layer would need a version compatible with (or require bumping) A2UI's pin — a cross-dependency conflict the ticket explicitly flags as needing evaluation, not yet resolved in code.
- **Auth-relevant code changes are unavoidable**: discovery endpoint (`GET .well-known/agent.json`) has a documented, recently-fixed public/private split (`is_global` bypass); any card-shape change must preserve that split and the `authenticate`/`Ability(user).can(Action.READ, ...)` checks. `get_auth_header()`'s four auth-type branches (Basic/API-key/Bearer/AWS SigV4) are CodeMie-specific and must survive whichever protocol layer is chosen — they are not part of any SDK's built-in auth handling.
- **Backward compatibility for previously connected remote agents (AC6) is undefined today**: no version negotiation, capability-mismatch handling, or explicit incompatibility error exists in the current client/server; `AgentCard.model_validate` in `A2ACardResolver` will simply fail or silently coerce on unexpected/missing fields from an old-shaped card.
- **No direct SDK usage anywhere to build from**: choosing the SDK-migration path is a from-scratch integration, not an incremental change — every type in `a2a/types.py`, every server/client method, and the router's JSON-RPC dispatch would need re-plumbing against SDK-native primitives.
- **Task persistence coupling**: `Task` is a SQLModel table (`a2a_tasks`) with a hand-rolled schema (`status`, `artifacts`, `history` stored via custom `PydanticType`/`PydanticListType` columns). A protocol-shape change to `Task`/`Message`/`Artifact` fields is also a persistence-layer and migration concern, not purely a wire-format concern.
- **Existing AWS Bedrock AgentCore integration** (`src/codemie/agents/aws_bedrock/agentcore_executor.py`, `bedrock_agentcore_endpoint_service.py`) is a live consumer of newer-spec-shaped events via the `BedrockAdapter` seam; any change to how the client normalizes events must not regress this integration, which has its own card-fetch special-case (`agent-card.json` for `bedrock-agentcore` URLs).

---

## 7. Summary for Complexity Assessment

This task touches four architectural layers: the API/router layer (`src/codemie/rest_api/routers/a2a.py`), a protocol/domain layer that is entirely in-house today (`src/codemie/rest_api/a2a/types.py`, `utils.py`, `client/*`, `server/*` — roughly 8 files, ~1,500 lines combined), the security layer (`authenticate`/`Ability`, already integrated and must be preserved), and the persistence layer (`Task` SQLModel / `a2a_tasks` table). CodeMie does not import the official `a2a-sdk` package anywhere in its own A2A code — the package is present in the dependency tree only transitively, pinned to `>=0.3.0,<0.4.0` by `a2ui-agent-sdk 0.5.0`, an unrelated feature (agent-authored UI surfaces, not Agent2Agent). Existing test coverage for the in-house stack is substantial (~2,400 lines across six files covering the router, client, card resolver, remote-agent-connection, task manager, and utils), giving a real regression baseline for an in-house upgrade but zero coverage transferable to an SDK migration.

Technical novelty is high on two fronts: CodeMie's current wire shapes (`authentication` field, `type`-discriminated parts, task-centric JSON-RPC method naming) trace to a pre-1.0 A2A draft, while a `BedrockAdapter` already present in the codebase demonstrates the newer spec's shape (`kind` discriminators, `message/stream`, `status-update`/`artifact-update` events) — meaning the gap between "current" and "target: latest published version" is concrete and already partially reverse-engineered in-repo, but not yet applied to CodeMie's own client/server types. Server-side streaming is currently a stub that raises `UnsupportedOperationError` despite a half-written, unused streaming generator already in `codemie_task_manager.py` — enabling it is new behavior, not a version-label change.

Key risk factors: the A2UI dependency conflict is real if the SDK path is chosen and must be resolved or explicitly deferred; backward compatibility for previously connected remote agents (AC6) has no existing mechanism (no version negotiation or incompatibility signaling today); and every auth-header-generation branch (Basic/API-key/Bearer/AWS SigV4) plus the recently-fixed public/private discovery-endpoint split are CodeMie-specific behaviors that any protocol-layer change (in-house upgrade or SDK migration) must reproduce rather than inherit from a library default.

---

## 8. External References

None named by the task. The ticket text is self-contained (protocol version and implementation approach are left as open decisions to document as acceptance criterion 1, not sourced from an external document or URL).
