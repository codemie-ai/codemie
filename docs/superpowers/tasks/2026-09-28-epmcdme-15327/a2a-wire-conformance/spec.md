# Spec — a2a-wire-conformance (EPMCDME-15327, slice 5)

Status: draft for user review. Authoritative sources: A2A v1.0.1 specification
(https://a2a-protocol.org/latest/specification/) and `specification/a2a.proto`
(https://github.com/a2aproject/A2A/blob/main/specification/a2a.proto). Where they differ on JSON
shape, the proto + ProtoJSON rules (field names camelCase, enums as their proto names) win.

## 1. Problem

Slices 1-4 chose v1.0, renamed methods, moved discovery, added ownership and legacy-card
handling. A source review against the proto showed the **body shapes are still a v0.3/v1.0
hybrid**, so an external v1.0 client (e.g. `a2a-sdk` >= 1.1) cannot interoperate with a CodeMie
assistant, and CodeMie cannot call a strict v1.0 agent. Acceptance criteria 2-5 and 7 of
EPMCDME-15327 are therefore only partially met.

## 2. Goal

The CodeMie A2A server and client exchange exactly the v1.0.1 JSON-RPC wire shapes for the five
supported methods: `SendMessage`, `SendStreamingMessage`, `GetTask`, `CancelTask`,
`GetExtendedAgentCard`. Data already stored, and legacy remote cards, keep loading (read-time
normalisation, no data migration). All protocol tests assert spec-derived fixtures, not
CodeMie's own model dumps.

## 3. Non-goals (unchanged accepted gaps)

- `ListTasks`, `SubscribeToTask`, push-notification config methods (answered with `-32601`).
- Agent card signing, multi-tenancy (`tenant`), extensions negotiation (`A2A-Extensions`).
- Cancel interrupting a running invocation; atomic cancel check.
- Bedrock AgentCore (v0.3) remotes; server-side v0.3 translation; `agent.json` route.
- `returnImmediately=true` background execution (rejected, see FR-S3).

## 4. Functional requirements

### 4.1 Types (`src/codemie/rest_api/a2a/types.py`)

- **FR-T1 TaskState** serializes as `TASK_STATE_SUBMITTED|WORKING|COMPLETED|FAILED|CANCELED|INPUT_REQUIRED|REJECTED|AUTH_REQUIRED`.
  Input additionally accepts the legacy values `submitted|working|completed|failed|canceled|input-required`
  (stored rows, old peers), mapped to the matching `TASK_STATE_*`.
- **FR-T1a** Legacy `unknown` is not a v1.0 state. It is normalised to `TASK_STATE_FAILED` on
  read, with a warning log, and is never emitted. `TASK_STATE_UNSPECIFIED` is not accepted on input.
- **FR-T2 Role** serializes as `ROLE_USER` / `ROLE_AGENT`; input also accepts legacy `user` / `agent`.
- **FR-T3 Message**: fields `messageId` (required; generated as uuid4 hex when absent on input),
  `contextId`, `taskId`, `role`, `parts`, `metadata`, `extensions`, `referenceTaskIds`. Empty
  `parts` is invalid.
- **FR-T4 Part**: exactly one of `text` | `raw` (base64 string) | `url` | `data` (any JSON value),
  plus optional `metadata`, `filename`, `mediaType`. No discriminator. Legacy stored/peer
  `{"file": {"bytes"|"uri", "name", "mimeType"}}` is normalised to `raw`/`url` + `filename` +
  `mediaType` by a before-validator; legacy `{"type": ...}` keys are ignored.
- **FR-T5 Task** serializes `{id, contextId, status, artifacts, history, metadata}`. The `id`
  field is named `id` (not `taskId`). The legacy `sessionId` is accepted on read as `contextId`.
  Server-only columns (`assistant_id`, `user_id`, dates) are never serialized.
- **FR-T6 TaskStatus**: `{state, message?, timestamp}`; timestamp is ISO-8601 UTC with `Z`.
- **FR-T7 Artifact**: `{artifactId (required; generated if absent), name, description, parts, metadata, extensions}`.
  `index`, `append`, `lastChunk` are removed from `Artifact` (legacy values ignored on read).
- **FR-T8 Events**: `TaskStatusUpdateEvent {taskId, contextId, status, metadata}` — no `final`.
  `TaskArtifactUpdateEvent {taskId, contextId, artifact, append, lastChunk, metadata}`.
  `StreamResponse` is an object with exactly one of `task`, `message`, `statusUpdate`, `artifactUpdate`.
  `SendMessageResponse` is an object with exactly one of `task`, `message`.
- **FR-T9 Requests**: `SendMessageRequest {tenant?, message, configuration?, metadata?}` with
  `SendMessageConfiguration {acceptedOutputModes, taskPushNotificationConfig, historyLength, returnImmediately}`;
  `GetTaskRequest {id, historyLength?}`; `CancelTaskRequest {id, metadata?}`; `GetExtendedAgentCardRequest {}`.
  The old `TaskSendParams` (`id`, `sessionId`, top-level `historyLength`) is removed.
- **FR-T10 AgentCard**: `name`, `description` (required), `supportedInterfaces[]`
  (`{url, protocolBinding, protocolVersion, tenant?}`), `provider {organization, url}`, `version`,
  `documentationUrl`, `capabilities {streaming, pushNotifications, extensions, extendedAgentCard}`,
  `securitySchemes`, `securityRequirements`, `defaultInputModes`/`defaultOutputModes` (media types,
  default `["text/plain"]`), `skills[]` (`id, name, description, tags` required; `examples,
  inputModes, outputModes`), `signatures`, `iconUrl`. `authentication` and
  `stateTransitionHistory` are removed. CodeMie-internal fields (`project_name`,
  `integration_id`, `user_id`, `bedrock_agentcore`) stay on the stored/remote card model and are
  **excluded from the public card JSON**.
- **FR-T11 Legacy card input** (extends slice-4 before-validator): `url`/`preferredTransport`/
  `additionalInterfaces` map to `supportedInterfaces`; interface `transport` maps to
  `protocolBinding`; a missing `protocolVersion` defaults to the card's top-level legacy
  `protocolVersion` if present, else `"1.0"`; legacy `authentication.schemes` is dropped (not
  mappable to `securitySchemes`); legacy `defaultInputModes: ["text"]` maps to `["text/plain"]`.
- **FR-T12 `get_jsonrpc_url`** matches `protocolBinding` case-insensitively (`JSONRPC`); the error
  type `AgentCardInterfaceError` is unchanged.

### 4.2 Server (`routers/a2a.py`, `server/codemie_task_manager.py`, `utils.py`)

- **FR-S1 Version gate.** The version is read from the `A2A-Version` header, else the
  `A2A-Version` query parameter. Missing/empty means `0.3` (spec §3.6). Matching is on
  `Major.Minor` (`1.0`, patch ignored). An unsupported version returns JSON-RPC error
  `-32009` `VersionNotSupportedError` whose `data` contains a `google.rpc.ErrorInfo` with
  `reason: "VERSION_NOT_SUPPORTED"` and `metadata.supportedVersions: "1.0"`. The gate runs after
  authentication/authorization and before body validation, on `POST /v1/a2a/assistants/{id}`
  only (the agent-card GET is exempt).
- **FR-S2 Task/context ids.** If `message.taskId` is absent a new task id is generated; if
  `message.contextId` is absent the existing task's context is reused, else a new id is
  generated. If `taskId` names an existing task the message continues it (ownership rules of
  slice 2 unchanged: not owner => `TaskNotFoundError`; terminal => `UnsupportedOperationError`).
  If both `taskId` and `contextId` are given and disagree with the stored task,
  `InvalidParamsError`.
- **FR-S3 SendMessage.** Result is `{"task": Task}`. `configuration.returnImmediately=true`
  returns `UnsupportedOperationError` (-32004). `configuration.acceptedOutputModes` is checked
  against `["text/plain"]` (`ContentTypeNotSupportedError` -32005 when disjoint).
  `configuration.historyLength` shapes the returned history as in `GetTask`.
  Agent output is a `text` part; `TASK_STATE_INPUT_REQUIRED` carries the message in
  `status.message`, `TASK_STATE_COMPLETED` carries one `Artifact` (with generated `artifactId`).
  Incoming `raw`/`url` parts remain unsupported (`InvalidParamsError`, "Only text parts are supported").
- **FR-S4 SendStreamingMessage.** SSE frames are `data: {"jsonrpc":"2.0","id":..,"result":StreamResponse}`.
  Order: `{task}` (state SUBMITTED/WORKING snapshot) -> `{statusUpdate}` WORKING -> optional
  `{artifactUpdate}` -> terminal or INPUT_REQUIRED `{statusUpdate}`. The stream closes after the
  last of those. A concurrent cancel yields a CANCELED `statusUpdate` and no artifact. Errors
  before the stream starts (validation, ownership) are sent as a single JSON-RPC error frame.
- **FR-S5 GetTask / CancelTask.** `id` param; unknown or not-owned => -32001; cancel of a terminal
  task => -32002 `TaskNotCancelableError`. `historyLength`: unset => full history, 0 => `history`
  omitted, N>0 => last N (spec §3.2.4).
- **FR-S6 Errors.** Unknown method or any out-of-scope method => `-32601`. Invalid params for a
  known method => `-32602`. Non-JSON body => `-32700`. Valid JSON that is not a JSON-RPC 2.0
  request => `-32600` (hint that A2A 1.0 is supported is retained). `error.data` is an array of
  objects each carrying `@type` (default `type.googleapis.com/google.rpc.ErrorInfo` with `reason`
  and `domain: "a2a-protocol.org"`); `-32602` uses `google.rpc.BadRequest` `fieldViolations`.
  All JSON-RPC errors, including pre-dispatch ones (version gate, -32700, -32600, -32601,
  -32602), are returned with HTTP 200 and the error object in the body, so JSON-RPC clients
  (including `a2a-sdk`) keep the error code. Amended after real-world verification against the
  official SDK client (was: HTTP 400 for pre-dispatch errors). Authentication/authorization
  failures keep their normal HTTP 401/403/404 responses.
- **FR-S7 Card.** `assistant_to_agent_card` emits FR-T10 shape:
  `supportedInterfaces=[{url, protocolBinding:"JSONRPC", protocolVersion:"1.0"}]`,
  `capabilities {streaming:true, pushNotifications:false, extendedAgentCard:true}`,
  `defaultInputModes/OutputModes=["text/plain"]`, `securitySchemes` describing the auth the
  endpoint really accepts (bearer/API key as configured for the deployment; if that cannot be
  determined statically, an `http` bearer scheme) with a matching `securityRequirements`.
  Discovery path, `is_global` auth split and the `Ability` check are unchanged.
- **FR-S8 Swagger body model** `A2ARequestBody` default example is updated to the v1.0 `SendMessage` shape.

### 4.3 Client (`client/*`, `utils.py`)

- **FR-C1** `A2AClient` builds v1.0 request bodies (`by_alias`, `exclude_none`) and parses
  `{task}` / `{message}` results into `SendMessageResponse`; streaming frames are parsed as
  `StreamResponse`. The tolerant bare-frame parser is removed.
- **FR-C2** `RemoteAgentConnections` and the chat handler consume the new types; a `message`
  result is converted to a completed pseudo-task result for the chat response.
- **FR-C3** A remote JSON-RPC error `-32601`, `-32600` or `-32009` on `SendMessage`/
  `SendStreamingMessage` raises `A2AProtocolVersionError` (subclass of `A2AClientError`) with the
  message "Remote agent does not support A2A protocol 1.0"; the handler surfaces it as a clear
  chat error (400-class), not a 500.
- **FR-C4** Card fetching keeps the slice-3 behaviour (`agent-card.json`, `A2A-Version` header) and
  normalises v0.3-style remote cards via FR-T11.
- **FR-C5** Auth-header generation (Basic/API key/Bearer/SigV4 helper) is unchanged.

### 4.4 Persistence

- **FR-P1** No data migration. Stored `a2a_tasks` JSON columns and stored assistant cards are
  read through the FR-T1..T11 normalisers; the next save writes v1.0 values.
- **FR-P2** Alembic `c9d8e7f6a5b4` is retained unchanged.

### 4.5 Documentation

- **FR-D1** `.ai-run/guides/integration/a2a-integration.md` and `version.py` are corrected: they
  list the verified shapes, drop the "supersedes" wording, describe legacy normalisation
  (including `preferredTransport`, `transport`, part/state/role), the strict version gate, the
  `-32601` scope rule and the `returnImmediately` limitation.

## 5. Testing requirements (in repo)

- **TR-1 Fixtures.** `tests/codemie/rest_api/a2a/fixtures/` holds v1.0 JSON fixtures derived from
  the spec examples and proto: agent card, SendMessage request/response (task and message
  variants), StreamResponse frames (task, statusUpdate, artifactUpdate), GetTask, CancelTask,
  GetExtendedAgentCard, and each supported error object.
- **TR-2 Contract tests** assert the exact serialized JSON of the CodeMie server (card, task,
  message, both events, errors) equals / is a superset-compatible match of the fixtures (key
  sets, enum values, envelope shape, absence of removed keys: `final`, `kind`, `type`, `taskId`
  on `Task`, `sessionId`, `authentication`, `transport`, `stateTransitionHistory`).
- **TR-3 Round-trip.** Each fixture parses into the CodeMie models and re-serializes unchanged.
- **TR-4 Legacy input.** One test per normaliser (state, role, file part, legacy artifact,
  `sessionId`, legacy card `url`/`transport`/`authentication`/`["text"]` modes, legacy stored task JSON).
- **TR-5 Version gate.** Missing header, `0.3`, `2.0`, `1.0`, `1.0.1`, and query-parameter variants.
- **TR-6 Method scope.** `ListTasks`, `SubscribeToTask`, push-config methods, `message/send` and
  garbage methods => `-32601`; malformed params => `-32602`; non-JSON => `-32700`.
- **TR-7 Server flows.** Send (new task, continue task, foreign task, terminal task, input-required,
  failed agent), streaming order and termination, cancel (ok / terminal / foreign / race), history length.
- **TR-8 Client.** Request bodies match fixtures; task and message results; stream envelopes;
  `A2AProtocolVersionError` mapping for -32601/-32600/-32009.
- Existing tests that assert the old shapes are rewritten, not deleted.

## 6. Acceptance criteria

1. For each in-scope method, CodeMie's serialized request/response/frame equals the v1.0 fixture
   shape (TR-2/TR-3 green); no removed key from TR-2 appears on the wire.
2. A request without `A2A-Version: 1.0` (or the query equivalent) is answered with `-32009`;
   with it, all five methods work.
3. Out-of-scope and unknown methods return `-32601`.
4. Legacy stored tasks and legacy remote cards still load (TR-4); no Alembic data migration added.
5. The client raises `A2AProtocolVersionError` for a remote that answers `-32601`/`-32600`/`-32009`.
6. The public agent card contains none of the CodeMie-internal fields and advertises only
   capabilities that work.
7. Guide and `version.py` match the implementation (FR-D1).
8. Ruff (`check` + `format --check`) and the scoped A2A pytest set pass in WSL.

## 7. Risks and notes

- **Breaking for CodeMie-internal callers of the old shapes** (`convert_to_task_request`,
  `convert_to_base_model_response`, `assistant_handlers`) — all are updated in this slice; grep
  for `TaskSendParams`, `sessionId`, `.final`, `FilePart`, `.artifacts[0]` before finishing.
- **Stored JSON compatibility** is the main regression risk: `PydanticType` columns deserialize
  through the models, so the normalisers must run inside model validation (before-validators), not
  only at the router.
- **Strict version gate** rejects header-less callers (curl/Swagger). This is the spec-mandated
  behaviour; the error `data` says which version to send.
- Alembic `c9d8e7f6a5b4` has never run on a real database; the plan's verification stage runs it
  (upgrade + downgrade) against the local Postgres.
- No local A2A SDK can be installed in this repo's environment (A2UI pins `a2a-sdk<0.4`), so
  cross-implementation checks are done out-of-repo during verification, not as repo tests.
