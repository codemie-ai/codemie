# A2A Integration

## Protocol Version Decision

**Decision:** upgrade the in-house A2A implementation (`src/codemie/rest_api/a2a/`) to the latest published A2A spec. Do not migrate to `a2a-sdk`.

**Target version: A2A spec v1.0** (negotiated value `"1.0"` per §3.6, which excludes patch numbers). Confirmed 2026-09-29 against the authoritative spec (https://a2a-protocol.org/latest/specification/, https://github.com/a2aproject/A2A/blob/main/docs/specification.md) and the `a2aproject/A2A` GitHub releases page: **v1.0.0** shipped 2026-03-12 with the breaking changes below; **v1.0.1** (2026-05-28) is a bug-fix-only patch (HTTP content-type preference, transcoding-error corrections, `TaskStatus` value clarifications) with no further wire-shape changes. `A2A_PROTOCOL_VERSION` in `src/codemie/rest_api/a2a/version.py` is now set to `"1.0"`.

**Correction (2026-09-29):** an earlier version of this guide named `kind` discriminators as the *target* marker, inferred from a since-removed Bedrock adapter in this repo and the transitively-installed `a2a-sdk==0.3.26` (pinned by `a2ui-agent-sdk`, an unrelated dependency). That was wrong: `kind` was the **v0.3.x** shape. v1.0.0's own breaking-change notes state **"Kind Discriminator Removed"** — confirmed by external verification before Slice 2 (server protocol) started. The removed Bedrock adapter reflected v0.3.x and is not a shape reference.

**Verified v1.0 wire-shape markers:**

- Discovery path is `/.well-known/agent-card.json` (renamed from `agent.json` in v0.3.0);
- **No `kind`/`type` discriminator on the wire** — each protocol binding (JSON-RPC / gRPC / REST) carries its own typing instead;
- JSON-RPC method names: `SendMessage`, `SendStreamingMessage`, `GetTask`, `ListTasks`, `CancelTask`, `SubscribeToTask`, `CreateTaskPushNotificationConfig` / `GetTaskPushNotificationConfig` / `ListTaskPushNotificationConfigs` / `DeleteTaskPushNotificationConfig`, `GetExtendedAgentCard` — not `message/send` / `message/stream` (that was pre-1.0 intermediate naming);
- `AgentCard.url` is removed, replaced by `supportedInterfaces[]` (one entry per transport binding) plus `signatures` (Agent Card signing, §8.4) and `capabilities.extendedAgentCard`;
- Field naming is camelCase (`messageId`, `contextId`, `taskId`, `artifactId`);
- Version negotiation via the `A2A-Version` request header; a server declares `"1.0"` (patch excluded); an empty header is interpreted as version `0.3` for backward compatibility — directly relevant to the `a2a-backward-compat` slice.
- New in v1.0.0 also relevant to later slices: native multi-tenancy (`tenant` field matched against the selected `AgentInterface`), modernized OAuth2 flows (device code/PKCE, implicit/password removed), simplified `Part` (flattened `FilePart`/`DataPart`), American spelling (`canceled`).

| Avoid | Prefer |
|---|---|
| Adopting `a2a-sdk` for CodeMie's A2A protocol layer | Upgrade the in-house implementation in `src/codemie/rest_api/a2a/`, keeping the existing test suite as a regression baseline |
| Treating the transitive `a2a-sdk==0.3.26` (v0.3.x) as the target shape reference | Treat the verified v1.0 markers above as authoritative |
| Re-deriving the wire-shape markers from scratch in a later slice | Reuse the verified v1.0 markers above: no discriminator, `agent-card.json`, `SendMessage`/`SendStreamingMessage`/etc., `supportedInterfaces[]`, `A2A-Version` header |

## Rationale

- **Effort:** an in-house upgrade keeps ~1,500 lines of protocol code and ~2,400 lines of existing tests as a regression baseline; an SDK migration transfers none of it.
- **Maintenance:** legacy shapes are handled by read-time before-validators on the in-house types (see Backward Compatibility), so an in-house upgrade needs no separate adapter layer.
- **A2UI conflict:** `a2ui-agent-sdk 0.5.0` pins `a2a-sdk` to `>=0.3.0,<0.4.0` (resolved `0.3.26`) for the unrelated A2UI feature. Adopting `a2a-sdk` for A2A itself would force CodeMie's protocol layer onto that constrained range, or require bumping A2UI's pin. Upgrading in-house avoids both.

## Wire Shapes (verified against `a2a.proto` / ProtoJSON)

The single source of truth for JSON shape is `specification/a2a.proto` (ProtoJSON rules: camelCase field names, enums as their proto names). Tests assert spec-derived fixtures in `tests/codemie/rest_api/a2a/fixtures/`, never CodeMie's own model dumps.

| Type | Wire shape |
|---|---|
| `TaskState` | `TASK_STATE_SUBMITTED` / `WORKING` / `COMPLETED` / `FAILED` / `CANCELED` / `INPUT_REQUIRED` / `REJECTED` / `AUTH_REQUIRED` |
| `Role` | `ROLE_USER` / `ROLE_AGENT` |
| `Part` | exactly one of `text`, `raw` (base64), `url`, `data`; optional `metadata`, `filename`, `mediaType`; no discriminator |
| `Message` | `messageId`, `contextId`, `taskId`, `role`, `parts`, `metadata`, `extensions`, `referenceTaskIds` |
| `Task` | `id` (not `taskId`), `contextId`, `status {state, message, timestamp}`, `artifacts`, `history`; timestamps are UTC ISO-8601 with `Z` |
| `Artifact` | `artifactId`, `name`, `description`, `parts`, `metadata`, `extensions` (no `index` / `append` / `lastChunk`) |
| Events | `TaskStatusUpdateEvent {taskId, contextId, status}` (no `final`), `TaskArtifactUpdateEvent {taskId, contextId, artifact, append, lastChunk}` |
| Results | `SendMessage` -> `{"task": Task}` or `{"message": Message}`; streaming frames -> `{"task"}`, `{"message"}`, `{"statusUpdate"}` or `{"artifactUpdate"}` (exactly one) |
| Requests | `SendMessage` / `SendStreamingMessage` params `{message, configuration {acceptedOutputModes, historyLength, returnImmediately}, metadata}`; `GetTask {id, historyLength}`; `CancelTask {id}`; `GetExtendedAgentCard {}` |
| Errors | JSON-RPC `error.data` is an array of typed objects: `google.rpc.ErrorInfo` (`reason`, `domain: "a2a-protocol.org"`, `metadata`) or `google.rpc.BadRequest` (`fieldViolations`, used for `-32602`) |
| `AgentCard` | `supportedInterfaces[{url, protocolBinding, protocolVersion, tenant}]`, `capabilities {streaming, pushNotifications, extendedAgentCard}`, `securitySchemes` (ProtoJSON oneof: `httpAuthSecurityScheme`, `apiKeySecurityScheme`, ...), `securityRequirements[{schemes: {name: {list: []}}}]`, `defaultInputModes` / `defaultOutputModes` as media types, `skills[{id, name, description, tags}]`; no `authentication`, no `stateTransitionHistory` |

## Server Behaviour

- **Version gate:** `POST /v1/a2a/assistants/{id}` reads `A2A-Version` from the header, else the `A2A-Version` query parameter. A missing or empty value means `0.3` (spec section 3.6) and, like any version whose `Major.Minor` is not `1.0`, is rejected with JSON-RPC `-32009` (`VersionNotSupportedError`; `ErrorInfo.reason = VERSION_NOT_SUPPORTED`, `metadata.supportedVersions = "1.0"`, HTTP 200 like every JSON-RPC error). Patch numbers are ignored (`1.0.1` passes). The gate runs after authentication and the access check, and before the body is parsed. The agent-card `GET` is exempt.
- **Error order:** non-JSON body `-32700`; not a JSON-RPC 2.0 request (or missing `method`) `-32600` (message states that protocol 1.0 is supported); unknown or out-of-scope method `-32601`; invalid params for a known method `-32602`. Every JSON-RPC error, pre-dispatch (version gate, `-32700`, `-32600`, `-32601`, `-32602`) or task-level (`-32001`, `-32002`, `-32004`, `-32005`), is answered with HTTP 200 and the error object in the body, so JSON-RPC clients (including `a2a-sdk`) keep the code. Authentication / authorization failures keep their normal HTTP 401/403/404.
- **Method scope:** `SendMessage`, `SendStreamingMessage`, `GetTask`, `CancelTask`, `GetExtendedAgentCard`. `ListTasks`, `SubscribeToTask`, the push-notification-config methods and every v0.3 method name (`message/send`, ...) answer `-32601`.
- **Ids:** the server assigns task ids; a message may name an existing, owned, non-terminal `taskId` (and matching `contextId`) to continue it. Unknown or foreign `taskId` -> `-32001`; terminal task -> `-32004`; `contextId` mismatch -> `-32602`. `contextId` is generated when absent.
- **`returnImmediately` limitation:** `configuration.returnImmediately = true` is rejected with `-32004`; background execution is not implemented, `SendMessage` always blocks until a terminal or `INPUT_REQUIRED` state. `acceptedOutputModes` must include `text/plain` (`-32005` otherwise). Only text parts are accepted (`-32602` for `raw` / `url` / `data`).
- **History:** `historyLength` unset returns the full history, `0` omits it, `N` returns the last `N` messages.
- **Streaming:** frames are ordered `{task}`, `{statusUpdate: WORKING}`, optional `{artifactUpdate}`, then a terminal or `INPUT_REQUIRED` `{statusUpdate}`; the stream closes after that. Errors before the stream starts are a single JSON-RPC error frame.
- **Public card:** the well-known card and `GetExtendedAgentCard` omit the CodeMie-internal fields (`project_name`, `integration_id`, `user_id`, `bedrock_agentcore`); `/v1/a2a/assistants/fetch` keeps them because the UI stores them. `securitySchemes` advertise a bearer JWT, plus the `user-id` header when `IDP_PROVIDER` is `local`.

## Client Behaviour

- The client always sends `A2A-Version: 1.0`, builds `SendMessage` / `SendStreamingMessage` bodies with `by_alias` + `exclude_none`, and parses results as `{task}` / `{message}` and streaming frames as `StreamResponse`.
- A remote that answers `-32601`, `-32600` or `-32009` (in an HTTP 200 body, on HTTP 4xx from other implementations, or as a streamed error frame) is not a v1.0 agent: the client raises `A2AProtocolVersionError` ("Remote agent does not support A2A protocol 1.0"), which the chat handler surfaces as a 400. On a streaming remote this holds when the rejection arrives before the first remote frame (the handler runs the remote call on a worker thread and waits for that first frame before returning the `StreamingResponse`); a rejection after the stream has started ends the chat answer with an in-chat `Error: ...`.
- Streams end on a terminal or `INPUT_REQUIRED` status (or a `message` result); there is no `final` flag.

## Backward Compatibility

All legacy handling is read-time normalisation in before-validators; there is no data migration and no Alembic data change.

- **Task / message JSON:** legacy task states (`submitted`, `input-required`, ...) map to `TASK_STATE_*`; the legacy `unknown` state becomes `TASK_STATE_FAILED` (with a warning) and is never emitted. Legacy roles `user` / `agent` map to `ROLE_*`. Legacy `{"file": {bytes|uri, name, mimeType}}` parts map to `raw` / `url` + `filename` + `mediaType`; `kind` / `type` keys, artifact `index` / `append` / `lastChunk` are ignored; missing `messageId` / `artifactId` are generated. `sessionId` of stored tasks is read as `contextId` (`task.contextId or task.sessionId`).
- **Legacy agent cards** (stored assistants and remote v0.3 cards): a top-level `url` (with `preferredTransport` and `additionalInterfaces`) becomes `supportedInterfaces[{url, protocolBinding, protocolVersion}]`; interface `transport` maps to `protocolBinding`; the card-level `protocolVersion` becomes the interface `protocolVersion` (default `0.3.0`) only for interfaces built from the legacy `url` — a card that already has `supportedInterfaces` keeps its own interface versions (default `1.0`), because a2a-sdk 1.x cards carry a compat `protocolVersion: "0.3"` next to genuine 1.0 interfaces; `authentication` is dropped; `["text"]` modes become `["text/plain"]`; v0.3 `securitySchemes` / `security` map to the ProtoJSON form; `supportsAuthenticatedExtendedCard` maps to `capabilities.extendedAgentCard`. When `supportedInterfaces` is non-empty it wins over `url`; a card with neither is invalid.
- **Missing JSON-RPC interface:** `get_jsonrpc_url` (binding matched case-insensitively) raises `AgentCardInterfaceError` (a `ValueError` subclass); the chat handler maps it to a 400 `ExtendedHTTPException`.
- **v0.3 clients:** not supported. They are rejected by the version gate (`-32009`); there is no server-side translation and no `agent.json` route.
- **Legacy `a2a_tasks` rows:** rows with NULL `assistant_id`/`user_id` fail closed as task-not-found; there is no back-fill.
- **Registration check:** `A2ACardResolver.fetch_agent_card` refuses a card with no JSON-RPC interface, or whose first JSON-RPC interface does not declare `Major.Minor` 1.0 (`is_supported_protocol_version`), so a v0.3 card cannot be registered.
- **Bedrock:** AgentCore remotes get no special handling (`bedrock-agentcore` hosts follow the same rules). The `bedrock_agentcore` card flag is still set from the hostname, only to tag the assistant `origin`.
- **Blocking work:** the server-side agent run (`SendMessage` and `SendStreamingMessage`) is executed with `asyncio.to_thread`, and the client-side streaming remote call runs on its own thread, so neither blocks the event loop (`performance-patterns.md`).

## Consuming Slices

This decision is implemented by four later slices:

- `a2a-server-protocol`
- `a2a-client-protocol`
- `a2a-backward-compat`
- `a2a-wire-conformance` (exact v1.0.1 body shapes, version gate, error model)

Evidence: `src/codemie/rest_api/a2a/utils.py` (hardcoded `version="1.0.0"` in `assistant_to_agent_card` — this is the *agent's own* card version field, coincidentally matching the spec number, not evidence of protocol-version support); `pyproject.toml` / `poetry.lock` (the transitive `a2a-sdk 0.3.26` pin via `a2ui-agent-sdk 0.5.0`); external verification 2026-09-29 against https://a2a-protocol.org/latest/specification/, https://github.com/a2aproject/A2A/blob/main/docs/specification.md, and https://github.com/a2aproject/A2A/releases (v1.0.0 2026-03-12, v1.0.1 2026-05-28).
