# A2A integration (protocol 1.0)

CodeMie speaks the Agent2Agent (A2A) protocol version 1.0 over the JSON-RPC binding, in both directions:

- **CodeMie calls a remote agent.** A remote A2A agent is registered as a *remote assistant*; chatting with it sends A2A requests to the agent.
- **An external client calls a CodeMie assistant.** Every CodeMie assistant is published as an A2A agent with its own Agent Card and JSON-RPC endpoint.

Everything in this document was executed against a running stack; the requests, responses and error outputs are recorded in `docs/superpowers/tasks/2026-10-01-epmcdme-15350/verification-report.md`. Related: [A2A wrapper guide](a2a-wrapper-guide.md) (how to put an A2A interface in front of your own agent). Demo agents used in the examples live in the separate `ai-run-demo-agents` repository (`python/agents/text-insights-agent` and `python/agents/howto-guide-agent`); they are examples only.

## 1. Setup

### 1.1 Enable remote assistants in the UI

The **Create Remote Assistant** button is controlled by the `remoteAssistant` feature flag in `config/customer/customer-config.yaml`:

```yaml
  - id: "remoteAssistant"
    settings:
      enabled: true
```

The customer config is cached for about 60 seconds; restart the `codemie` service to apply it at once. Publishing CodeMie assistants over A2A needs no flag.

### 1.2 Environment variables

Defined in `src/codemie/configs/config.py`:

| Variable | Default | Meaning |
|---|---|---|
| `A2A_AGENT_CARD_FETCH_TIMEOUT` | `30.0` (seconds) | timeout when CodeMie downloads a remote Agent Card |
| `A2A_AGENT_REQUEST_TIMEOUT` | `30.0` (seconds) | timeout for a non-streaming request to a remote agent; raise it for slow LLM-backed agents |
| `A2A_PROVIDER_ORGANIZATION` | empty | filled into `provider.organization` of CodeMie-published cards |
| `A2A_PROVIDER_URL` | empty | filled into `provider.url` of CodeMie-published cards |

### 1.3 Authentication

**Inbound (external client to CodeMie).** The JSON-RPC endpoint always requires credentials. A private assistant's card also requires them; the card of a *global* assistant (`is_global`) is public.

- `IDP_PROVIDER=local` with `ENABLE_USER_MANAGEMENT=true`: log in with `POST /v1/local-auth/login` (`{"email": ..., "password": ...}`) and send `Authorization: Bearer <access_token>`. The token lasts 24 hours.
- Other identity providers: send the bearer token your IDP issues.
- The card also advertises a `user-id` header scheme. With `IDP_PROVIDER=local` that header is accepted only when `ENV=local`; with any other `ENV` (for example `development`) a request carrying only `user-id` gets `401`. Use a bearer token.

```bash
export TOKEN=$(curl -s -X POST localhost:8080/v1/local-auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@codemie.ai","password":"password"}' | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")
```

**Outbound (CodeMie to a remote agent).** If the remote agent needs authentication, create an *A2A* integration in the same project (credential type A2A) and pick it in the **Integration** field when registering the remote assistant. Credential styles supported by the code (`SettingsService.get_a2a_creds`): bearer (default), basic, API key (custom header) and AWS signature. *Not exercised live in verification: the demo agents have no authentication, so only the no-credentials path was run.* Agents without authentication need no integration.

## 2. CodeMie calls a remote agent

> The UI click path below is taken from the UI source and was **not** run in verification. What was run live is the equivalent REST sequence: `GET /v1/a2a/assistants/fetch` (registration fetch), `POST /v1/assistants` with the fetched `agent_card`, then `POST /v1/assistants/{id}/model` (chat).

1. In the UI open **Assistants**, then **Create Remote Assistant**.
2. Enter the project, the agent's **base URL** (no path; CodeMie appends `/.well-known/agent-card.json`) and optionally an integration.
3. Use the fetch control next to the URL: CodeMie downloads the Agent Card (the same call is `GET /v1/a2a/assistants/fetch?url=...&project_name=...`). Save, then chat with the new assistant.

On the wire CodeMie sends `A2A-Version: 1.0` on every request (the remote must implement protocol 1.0; see section 6). A non-streaming agent such as `text-insights-agent` is called with `SendMessage` and answers with a `message`; a streaming agent such as `howto-guide-agent` is called with `SendStreamingMessage` and answers with `text/event-stream`: a `task` frame, one `artifactUpdate` per result part, then a final `statusUpdate` with `TASK_STATE_COMPLETED`. CodeMie reads until the final state and shows the result in chat; artifacts are streamed to the chat as incremental chunks.

Chat through the REST API (this is what the UI uses):

```bash
curl -s -X POST localhost:8080/v1/assistants/<remote-assistant-id>/model \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"text":"Customer says: The app keeps crashing after the last update ...","stream":false}'
```

Use `"stream":true` for incremental output.

**Docker note.** An agent advertises its own address in its card (`supportedInterfaces[].url`), and CodeMie later calls that address. When CodeMie runs in Docker, run the agent on the same Docker network and start it with `--host <container-name>`, so the card says `http://<container-name>:<port>/` (a card saying `http://0.0.0.0:<port>/` points at the CodeMie container itself and fails).

## 3. An external client calls a CodeMie assistant

| What | URL |
|---|---|
| Agent Card | `GET /v1/a2a/assistants/{assistant_id}/.well-known/agent-card.json` |
| JSON-RPC endpoint | `POST /v1/a2a/assistants/{assistant_id}` |

Every JSON-RPC request must carry the header `A2A-Version: 1.0` (or the query parameter `A2A-Version=1.0`).

```bash
export CM=http://localhost:8080/v1/a2a/assistants
export AID=<assistant id>
```

### Agent Card

```bash
curl -s -H "Authorization: Bearer $TOKEN" $CM/$AID/.well-known/agent-card.json
```

The card has `supportedInterfaces[0]` = `{"url": ".../v1/a2a/assistants/<id>", "protocolBinding": "JSONRPC", "protocolVersion": "1.0"}`, `capabilities` = `streaming: true, pushNotifications: false, extendedAgentCard: true`, `securitySchemes`, and a single `conversation` skill.

### SendMessage

```bash
curl -s -X POST $CM/$AID -H "Authorization: Bearer $TOKEN" -H "A2A-Version: 1.0" -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"SendMessage","params":{"message":{"messageId":"m-1","role":"ROLE_USER","parts":[{"text":"Say hello"}]}}}'
```

The result is a completed task:

```json
{"jsonrpc":"2.0","id":1,"result":{"task":{"contextId":"a6aa...","id":"95eb5bfd82474d95bb94fa52de9e8647",
  "status":{"state":"TASK_STATE_COMPLETED","timestamp":"2026-10-01T14:48:52.072404Z"},
  "artifacts":[{"artifactId":"6c4c...","parts":[{"text":"Hello!"}]}],
  "history":[{"messageId":"m-1","role":"ROLE_USER","parts":[{"text":"Say hello"}]}]}}}
```

Pass `"taskId"` inside `message` to continue a task that is still open.

### SendStreamingMessage

```bash
curl -N -X POST $CM/$AID -H "Authorization: Bearer $TOKEN" -H "A2A-Version: 1.0" -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":3,"method":"SendStreamingMessage","params":{"message":{"messageId":"m-2","role":"ROLE_USER","parts":[{"text":"Say hello"}]}}}'
```

Server-sent events, in order: `task` (`TASK_STATE_SUBMITTED`), `statusUpdate` (`TASK_STATE_WORKING`), one or more `artifactUpdate`, final `statusUpdate` (`TASK_STATE_COMPLETED`). There is no `final` flag and no `kind` field; the stream ends after the terminal state.

### GetTask

```bash
curl -s -X POST $CM/$AID -H "Authorization: Bearer $TOKEN" -H "A2A-Version: 1.0" -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":2,"method":"GetTask","params":{"id":"<task id from SendMessage>","historyLength":5}}'
```

`historyLength: 0` omits the history.

## 4. Supported operations

| Method | Support |
|---|---|
| `SendMessage` | supported (blocking; result is a `task`) |
| `SendStreamingMessage` | supported (SSE) |
| `GetTask` | supported |
| `CancelTask` | supported; a completed task answers `-32002` (`TASK_NOT_CANCELABLE`) |
| `GetExtendedAgentCard` | supported (returns the agent card) |
| `ListTasks`, `SubscribeToTask`, push-notification methods | not implemented, `-32601` |

## 5. Known limitations

- Protocol version 1.0 only. Pre-1.0 method names (`message/send`, `tasks/send`) are not recognised (`-32601`), and a request without `A2A-Version: 1.0` is rejected with `-32009`. There is no v0.3 translation: a remote whose card declares a pre-1.0 JSON-RPC interface is refused at registration, and one that serves a 1.0 card but rejects 1.0 calls fails at chat time. Bedrock AgentCore hosts get no special handling; they are accepted or refused by the same rules.
- Text parts only: messages carry text, and the published card declares `text/plain` input and output modes. A request with `acceptedOutputModes` that exclude `text/plain` (for example `image/png`) answers `-32005`.
- `configuration.returnImmediately` is not supported (`-32004`).
- `ListTasks`, `SubscribeToTask` and push notifications are not implemented (`-32601`); the card says `pushNotifications: false`. Agent Card signing and multi-tenancy are not implemented.
- Protocol errors are returned with **HTTP 200** and a JSON-RPC `error` object, so every JSON-RPC client keeps the error code. Authentication and lookup failures are plain HTTP errors with a CodeMie `{"error":{"message":...}}` body: `401` missing or invalid credentials, `403` no access to the assistant, `404` unknown assistant.
- An exception inside a CodeMie assistant run is reported as task state `TASK_STATE_INPUT_REQUIRED`, not `FAILED`.

Protocol error codes seen in verification (all HTTP 200):

| Cause | Code |
|---|---|
| missing or unsupported `A2A-Version` | `-32009` |
| unknown or removed method (`message/send`, `ListTasks`, `SubscribeToTask`) | `-32601` |
| invalid params (for example empty `parts`) | `-32602` |
| unknown task | `-32001` |
| task not cancelable | `-32002` |
| unsupported operation (`returnImmediately`) | `-32004` |
| content type not supported | `-32005` |

## 6. Failure behaviour when CodeMie calls a remote agent

Observed outputs (more detail in the verification report):

| Situation | What the user sees |
|---|---|
| Agent not running or host unresolvable | registration: HTTP 400, `Error fetching agent card: [Errno -2] Name or service not known`; chat: HTTP 500 `Assistant Error` with `ConnectError: ...` |
| Agent port closed | chat: HTTP 500 `Assistant Error`, `ConnectError: All connection attempts failed` |
| Agent slower than `A2A_AGENT_REQUEST_TIMEOUT` | chat: HTTP 500 `Assistant Error`, `ReadTimeout`, after about 30 s |
| Agent card slower than `A2A_AGENT_CARD_FETCH_TIMEOUT` | registration: HTTP 400 `Error fetching agent card: ` (empty reason), after about 30 s |
| Card is not valid JSON | registration: HTTP 400 `Invalid agent card format: Expecting property name enclosed in double quotes ...` |
| No v1.0 card (legacy agents serve only `agent.json`) | registration: HTTP 400 `Error fetching agent card: HTTP 404` |
| Card declares no JSON-RPC interface | registration: HTTP 400 `Error fetching agent card: Agent card has no JSON-RPC interface` |
| Card declares a pre-1.0 JSON-RPC interface (a v0.3 card) | registration: HTTP 400 `Error fetching agent card: Agent does not support A2A protocol 1.0 (its JSON-RPC interface declares 0.3.0)` |
| Remote card says 1.0 but the remote rejects 1.0 calls | chat: HTTP 400 `Remote A2A agent does not support the required protocol version` / `Remote agent does not support A2A protocol 1.0`, for both streaming and non-streaming remotes (if the rejection comes after the stream has started, it ends the chat answer with `Error: ...` instead) |
| Missing or invalid credentials for an inbound call | `401` `Authentication required` or `Invalid JWT: ...` |
| Inbound call to someone else's private assistant | `403` `Access Denied` |

If the registered card contains legacy top-level fields (`url`, `preferredTransport`, `protocolVersion: "0.3"`) next to `supportedInterfaces`, as the demo agents' cards do, CodeMie accepts it: the interfaces' own versions (default `1.0`) are kept, and the legacy `protocolVersion` is used only for a card that has no `supportedInterfaces`.

The two registration rows above come from the code and its unit tests; they were not re-run in the live verification.

## 7. Troubleshooting

| Symptom | Fix |
|---|---|
| fetch says `HTTP 404` | wrong base URL (do not include `/.well-known/...` or a path), or the agent only supports an older protocol: check `curl <url>/.well-known/agent-card.json` |
| fetch works, chat fails with `ConnectError` | the card advertises an address the CodeMie container cannot reach (for example `http://0.0.0.0:<port>/`); recreate the agent with `--host <container-name>` on the CodeMie Docker network |
| `-32009` on every call | add `A2A-Version: 1.0` |
| `401` from curl | token missing or expired (24 h); log in again; `user-id` alone does not work unless `ENV=local` |
| slow first answer | the remote agent calls an LLM; raise `A2A_AGENT_REQUEST_TIMEOUT` |
