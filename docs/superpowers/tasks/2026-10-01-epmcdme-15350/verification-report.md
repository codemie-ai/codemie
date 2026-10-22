# EPMCDME-15350 Live verification report

Run date: 2026-10-01. Branch `EPMCDME-15327_upgrade-a2a-protocol` (HEAD `e6fd044be` at start).
Every request below was executed against the running local stack; responses are real and trimmed only where marked `...`.

## Environment

| Item | Value |
|---|---|
| CodeMie API | container `codemie-dev-codemie-1`, `localhost:8080` (WSL Ubuntu Docker, compose project `codemie-dev`) |
| Postgres / Elasticsearch | `codemie-dev-postgres-1` :5432, `codemie-dev-elasticsearch-1` :9200 |
| Agents | containers `text-insights-agent` :10800, `howto-guide-agent` :10900, `currency-converter` :10000, `research-agent` :10700 (already running on the compose network; images built from `ai-run-demo-agents`) |
| Auth mode | `IDP_PROVIDER=local`, `ENABLE_USER_MANAGEMENT=true`, `ENV=development` (so the `user-id` header is NOT accepted, see Task 2) |
| Test user | local superadmin from `.env` (`SUPERADMIN_EMAIL`/`SUPERADMIN_PASSWORD`), JWT from `POST /v1/local-auth/login` |
| Assistants used | `Brevity Buddy` (native, private) `3ad89bf6-a613-4253-8a9f-d8927574fcd0`; remote `Text Insights Agent` `26f63cf8-d6a9-4302-9046-a81c1301be2e`; remote `How-To Guide Agent` `b032bb62-f095-413f-af09-5f048e18665e` |

Login (token stored locally, not recorded here):

```bash
curl -s -X POST localhost:8080/v1/local-auth/login -H "Content-Type: application/json" \
  -d '{"email":"admin@codemie.ai","password":"password"}'      # -> {"access_token": "...", "user": {...}}
```

The remote assistants were already registered in the UI before this run; the registration click path itself was not re-run.

## Task 1: Happy path

### 1.1 Agent Cards fetched directly from the agents

```bash
curl -s localhost:10800/.well-known/agent-card.json      # HTTP 200
curl -s localhost:10900/.well-known/agent-card.json      # HTTP 200
```

`text-insights-agent` (trimmed):

```json
{"name":"Text Insights Agent","supportedInterfaces":[{"url":"http://text-insights-agent:10800/","protocolBinding":"JSONRPC"}],
 "version":"1.0.0","capabilities":{"streaming":false},"skills":[{"id":"analyze_text_insights",...}],
 "preferredTransport":"JSONRPC","protocolVersion":"0.3","url":"http://text-insights-agent:10800/"}
```

`howto-guide-agent` is identical in shape with `"capabilities":{"streaming":true}` and url `http://howto-guide-agent:10900/`.

Observation (correction for the doc): these agent cards carry BOTH the v1.0 `supportedInterfaces` and legacy top-level `url`, `preferredTransport`, `protocolVersion: "0.3"`; the interface entry has no `protocolVersion`. CodeMie accepts them (registration and chat work). The agent still enforces `A2A-Version: 1.0` on requests (1.2). DEMO-GUIDE implies a clean v1.0 card.

### 1.2 Agent wire behaviour, direct

Non-streaming:

```bash
curl -s -X POST localhost:10800/ -H "A2A-Version: 1.0" -H "Content-Type: application/json" -d '{"jsonrpc":"2.0","id":"1","method":"SendMessage","params":{"message":{"messageId":"d1","role":"ROLE_USER","parts":[{"text":"Customer says: great product, slow shipping."}]}}}'
```

```
HTTP 200
{"result":{"message":{"messageId":"e3701f54-...","role":"ROLE_AGENT","parts":[{"text":"Status: completed\nSentiment: positive with room for improvement\nKey points: ...\nSuggested next step: ..."}]}},"id":"1","jsonrpc":"2.0"}
```

Streaming (`howto-guide-agent`, `SendStreamingMessage`, prompt "Boil an egg."), 8 frames: `task` (SUBMITTED) -> `artifactUpdate` `step-1` ... `step-5` -> `artifactUpdate` `guide` -> `statusUpdate` `TASK_STATE_COMPLETED` (trimmed):

```
data: {"result": {"task": {"id": "ef1947b2-...", "contextId": "8cf1416d-...", "status": {"state": "TASK_STATE_SUBMITTED"}, ...}}}
data: {"result": {"artifactUpdate": {"taskId": "ef1947b2-...", "artifact": {"artifactId": "step-1", "parts": [{"text": "1. Place an egg in a saucepan ..."}]}}}}
...
data: {"result": {"statusUpdate": {"taskId": "ef1947b2-...", "status": {"state": "TASK_STATE_COMPLETED", ...}}}}
```

Version gate on the agent: the same `SendMessage` WITHOUT `A2A-Version` returns HTTP 200 with `error.code -32009`, message `A2A version '0.3' is not supported by this handler. Expected version '1.0'.`. Because CodeMie chat succeeds against this agent (1.3), CodeMie sends `A2A-Version: 1.0` (code: `src/codemie/rest_api/a2a/client/client.py:222`, `card_resolver.py:88`).

### 1.3 CodeMie calls the remote agents (client direction)

Non-streaming, remote assistant -> `SendMessage`:

```bash
curl -s -X POST localhost:8080/v1/assistants/26f63cf8-d6a9-4302-9046-a81c1301be2e/model -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" -d '{"text":"Customer says: The app keeps crashing after the last update and support has not answered my ticket for 3 days. I am really disappointed.","stream":false}'
```

```
HTTP 200
{"generated":"Status: completed\nSentiment: Negative\nKey points: The customer reports the app crashes following the recent update.; Support has not responded to their ticket in 3 days.; ...\nSuggested next step: Address the identified issue ...","success":true,"agentError":null,...}
```

Streaming, remote assistant -> `SendStreamingMessage`, prompt "Set up a home Wi-Fi router.", `"stream":true`: HTTP 200, NDJSON-style chunks; first a `thought` frame (`author_name":"How-To Guide Agent"`), then one `generated_chunk` per step ("1. Unbox the Wi-Fi router ...", "\n\n2. Connect the router ...", ... ), run to completion (`last` frame at the end). The `text-insights-agent` container log shows `POST / HTTP/1.1 200 OK` from the CodeMie container (172.18.0.4) for each call.

### 1.4 External client calls a CodeMie assistant (server direction)

Agent Card (private assistant, bearer):

```bash
curl -s -H "Authorization: Bearer $TOKEN" localhost:8080/v1/a2a/assistants/3ad89bf6-.../.well-known/agent-card.json
```

```
HTTP 200
{"name":"Brevity Buddy","description":"...","supportedInterfaces":[{"url":"http://localhost:8080/v1/a2a/assistants/3ad89bf6-...","protocolBinding":"JSONRPC","protocolVersion":"1.0"}],
 "provider":{"organization":"","url":""},"version":"1.0.0",
 "capabilities":{"streaming":true,"pushNotifications":false,"extendedAgentCard":true},
 "securitySchemes":{"bearer":{"httpAuthSecurityScheme":{"scheme":"bearer","bearerFormat":"JWT"}},"user-id":{"apiKeySecurityScheme":{"location":"header","name":"user-id"}}},
 "securityRequirements":[{"schemes":{"bearer":{"list":[]}}},{"schemes":{"user-id":{"list":[]}}}],
 "defaultInputModes":["text/plain"],"defaultOutputModes":["text/plain"],"skills":[{"id":"conversation","name":"Conversation",...}]}
```

Note: the card advertises a `user-id` apiKey scheme even though it is rejected when `ENV != local` (see Task 2).

`SendMessage`:

```bash
curl -s -X POST localhost:8080/v1/a2a/assistants/$AID -H "Authorization: Bearer $TOKEN" -H "A2A-Version: 1.0" -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"SendMessage","params":{"message":{"messageId":"m-1","role":"ROLE_USER","parts":[{"text":"Say hello"}]}}}'
```

```
HTTP 200
{"jsonrpc":"2.0","id":1,"result":{"task":{"contextId":"a6aa5f4f...","id":"95eb5bfd82474d95bb94fa52de9e8647","status":{"state":"TASK_STATE_COMPLETED","timestamp":"2026-10-01T14:48:52.072404Z"},"artifacts":[{"artifactId":"6c4c6c30...","parts":[{"text":"Hello!"}]}],"history":[{"messageId":"m-1","contextId":"a6aa5f4f...","taskId":"95eb5bfd...","role":"ROLE_USER","parts":[{"text":"Say hello"}]}]}}}
```

`GetTask` with `"params":{"id":"95eb5bfd82474d95bb94fa52de9e8647","historyLength":5}`: HTTP 200, same task (state COMPLETED, artifact "Hello!", history of 1).

`SendStreamingMessage` (`-N`), HTTP 200, `text/event-stream`, 4 frames in order: `task` (SUBMITTED, with history) -> `statusUpdate` (WORKING) -> `artifactUpdate` (`"Hello! 👋"`) -> `statusUpdate` (COMPLETED). No `final` flag, no `kind`.

Frame arrival times for a longer prompt ("Write a 120-word story about a fox."), seconds within the minute: `28.395` (task), `29.125` (WORKING), `29.995` (artifact), `30.766` (COMPLETED). Frames are spread over about 2.4 s, i.e. they are not delivered all at once at the end.

Other operations:

| Request | Result |
|---|---|
| `GetExtendedAgentCard` (`params:{}`) | HTTP 200, result is the agent card (name "Brevity Buddy", ...) |
| `CancelTask` on the completed task | HTTP 200, `error.code -32002`, `TASK_NOT_CANCELABLE` |
| `ListTasks` | HTTP 200, `error.code -32601`, `METHOD_NOT_FOUND` |
| `GetTask`, no `A2A-Version` header | HTTP 200, `error.code -32009`, `VERSION_NOT_SUPPORTED`, `metadata.supportedVersions: "1.0"` |

### 1.5 Deviations from DEMO-GUIDE to carry into the doc

1. Agent cards also contain legacy `protocolVersion: "0.3"`/`url`/`preferredTransport`; CodeMie tolerates it (1.1).
2. The DEMO-GUIDE limit "streaming is not incremental on the CodeMie server, frames arrive together" did not reproduce: frames arrived about 0.7-0.9 s apart (1.4). Doc states streaming frames are emitted as the run progresses and does not repeat the old limit.
3. The agent containers were already on the compose network with `--host <container-name>`; the card URLs (`http://text-insights-agent:10800/`) resolve from the CodeMie container (chat works), which confirms the Docker layout of DEMO-GUIDE section 3.
4. `user-id` header: advertised in the card, rejected unless `ENV=local` (Task 2).

## Task 2: Failure and auth runs

Method: a throwaway stub server (container `stub` on `codemie-dev_codemie-network`, port 9000, removed afterwards) served the broken Agent Cards and a v0.3-style RPC endpoint. Remote assistants `zz-*` were registered through `GET /v1/a2a/assistants/fetch` + `POST /v1/assistants` (with `agent_card`), exercised through `POST /v1/assistants/{id}/model`, then deleted. Defaults in force: `A2A_AGENT_CARD_FETCH_TIMEOUT=30`, `A2A_AGENT_REQUEST_TIMEOUT=30`.

Two layers are involved. Registration calls `GET /v1/a2a/assistants/fetch?url=...&project_name=...` (this is what the UI "fetch" control calls). Chat calls `POST /v1/assistants/{id}/model`. Errors from calls CodeMie makes OUTBOUND to a remote agent are plain CodeMie REST errors (HTTP 400/500 with `{"error":{...}}`); JSON-RPC error bodies with HTTP 200 only appear on the INBOUND `/v1/a2a/assistants/{id}` endpoint.

### 2.1 Outbound: remote agent problems

| Scenario | Request | HTTP | Surfaced error |
|---|---|---|---|
| Agent stopped, at registration (`docker stop text-insights-agent`) | `GET /v1/a2a/assistants/fetch?url=http://text-insights-agent:10800` | 400 | `details: 'Error fetching agent card: [Errno -2] Name or service not known'` (a stopped container's DNS name disappears from the compose network; on a reachable host with a closed port the text is a connect error, see the next rows) |
| Agent stopped, at chat (same stop) | `POST /v1/assistants/26f63cf8-.../model` `{"text":"hello","stream":false}` | 500 | `message: "Assistant Error"`, `details: "ConnectError: [Errno -2] Name or service not known"` (about 2 s) |
| Port closed, card already registered (`stub:9999`) | chat, `stream:false` and `stream:true` | 500 | `details: "ConnectError: All connection attempts failed"` |
| Agent too slow on RPC (`stub` sleeps 45 s) | chat | 500 after about 31 s | `details: "ReadTimeout"` (governed by `A2A_AGENT_REQUEST_TIMEOUT`, 30 s) |
| Agent too slow on card fetch (sleeps 45 s) | `GET .../fetch?url=http://stub:9000/slow` | 400 after about 31 s | `details: "'Error fetching agent card: '"` (empty reason: the timeout exception has no message; governed by `A2A_AGENT_CARD_FETCH_TIMEOUT`, 30 s) |
| Card is not JSON (`{not valid json`) | `GET .../fetch?url=http://stub:9000/badjson` | 400 | `details: "'Invalid agent card format: Expecting property name enclosed in double quotes: line 1 column 2 (char 1)'"` |
| Card without any interface (`"supportedInterfaces":[]`, no `url`) | fetch | 200 (accepted, card returned with empty `supportedInterfaces`) | none at registration; `POST /v1/assistants` also succeeds |
| ...same assistant, at chat | chat | 400 | `message: "Remote A2A agent is not usable"`, `details: "Agent card has no JSON-RPC interface"`, `help: "Re-register the remote agent so its card declares a JSON-RPC interface (A2A protocol v1.0)."` |
| Card with only a `GRPC` interface | fetch / chat | 200 / 400 | same as the row above |
| Legacy agent, no v1.0 card (`currency-converter`, serves only `/.well-known/agent.json`) | fetch `http://currency-converter:10000` | 400 | `details: "'Error fetching agent card: HTTP 404'"` |
| Wrong path | fetch `http://text-insights-agent:10800/nope` | 400 | `'Error fetching agent card: HTTP 404'` |
| Unresolvable host | fetch `http://no-such-agent:1234` | 400 | `'Error fetching agent card: [Errno -2] Name or service not known'` |
| Legacy agent behind a hand-made card, real call (card `url` -> `http://currency-converter:10000/`) | chat, `stream:false` and `stream:true` | 400 | `message: "Remote A2A agent does not support the required protocol version"`, `details: "Remote agent does not support A2A protocol 1.0"`, `help: "The remote agent must implement A2A protocol v1.0 (JSON-RPC binding)."` |
| v0.3 handler answering `HTTP 400 {"error":{"code":-32600,...}}` (stub) | chat | 400 | same version error as the row above |

### 2.2 Inbound: authentication and authorization

Credentials: superadmin JWT (`$TOKEN`) and a second local user `zz-other@example.com` (JWT `$T2`). Private assistant `3ad89bf6-...` (Brevity Buddy); a temporary global assistant `zz-global` was created and deleted.

| Request | HTTP | Body |
|---|---|---|
| Card, private assistant, no auth | 401 | `{"error":{"message":"Authentication required",...}}` |
| Card, private, `Authorization: Bearer not.a.jwt` | 401 | `{"error":{"message":"Invalid JWT: Invalid header string: 'utf-8' codec can't decode byte 0x9e in position 0: invalid start byte"}}` |
| Card, private, `user-id: <admin id>` header (ENV=development) | 401 | `Authentication required` |
| Card, private, other user's bearer | 403 | `Access Denied` / `You don't have permission to access this assistant.` |
| Card, unknown assistant id | 404 | `Assistant with id does-not-exist wasn't found` |
| Card, global assistant, no auth | 200 | public card |
| JSON-RPC `GetTask`, private, no auth | 401 | `Authentication required` |
| JSON-RPC, private, invalid bearer | 401 | `Invalid JWT: ...` |
| JSON-RPC, private, `user-id` header only | 401 | `Authentication required` |
| JSON-RPC, private, other user's bearer | 403 | `Access Denied` |
| JSON-RPC, unknown assistant | 404 | `Assistant with id does-not-exist wasn't found` |
| JSON-RPC `GetTask`, global assistant, no auth | 401 | `Authentication required` (the JSON-RPC endpoint always needs credentials) |
| JSON-RPC `SendMessage`, global assistant, other user's bearer | 200 | completed task, artifact "Hello! How can I help you today? ..." |

Auth failures keep plain HTTP 401/403/404 with the CodeMie `{"error":{...}}` body; protocol errors (-32009, -32002, -32601, from 1.4) are HTTP 200 with a JSON-RPC `error`.

Cleanup performed: all `zz-*` assistants and the `stub` container removed; `text-insights-agent` restarted and verified (card HTTP 200). The local user `zz-other@example.com` was created for the 403 check and was not deleted: it remains in the local DB and should be removed by the operator if unwanted.

## Open risks

1. **Empty timeout reason on card fetch.** A card fetch timeout surfaces as `Error fetching agent card: ` (nothing after the colon), because the httpx timeout exception has an empty message (`card_resolver.py`, `str(e)`). The user cannot tell a timeout from other failures. Not fixed (out of scope).
2. **Card with no usable interface is accepted at registration.** `fetch` returns 200 and the assistant saves; the failure appears only at chat ("Remote A2A agent is not usable"). Not fixed.
3. **Remote connection and timeout errors surface as HTTP 500 `Assistant Error`** with the raw exception text (`ConnectError: ...`, `ReadTimeout`), while version and interface problems are typed HTTP 400 errors. Not fixed.
4. **Card advertises a `user-id` security scheme that is rejected** unless `ENV=local` (401 with `ENV=development`); external clients following the card will fail on that scheme in non-local deployments. Not fixed.
5. **Agent cards from the demo agents carry legacy `protocolVersion: "0.3"`** next to `supportedInterfaces`. CodeMie tolerates it, but a strict v1.0 consumer may not. This is a demo-agent concern, recorded for completeness.

## Task 3 support: additional protocol-error checks (run live before writing the doc)

All against `POST /v1/a2a/assistants/3ad89bf6-...` with a valid bearer and `A2A-Version: 1.0` unless stated. Every call returned HTTP 200 with a JSON-RPC `error`:

| Request | `error.code` / reason |
|---|---|
| `SendMessage` with `"configuration":{"returnImmediately":true}` | `-32004` `UNSUPPORTED_OPERATION`, `returnImmediately is not supported` |
| `SendMessage` with `"configuration":{"acceptedOutputModes":["image/png"]}` | `-32005` `CONTENT_TYPE_NOT_SUPPORTED` |
| method `message/send` (pre-1.0 name) | `-32601` `METHOD_NOT_FOUND` |
| `SendMessage` with `"parts":[]` | `-32602`, `BadRequest.fieldViolations[message.parts]` |
| `SubscribeToTask` | `-32601` |
| `GetTask` with id `nope` | `-32001` `TASK_NOT_FOUND` |
| `A2A-Version: 0.3` | `-32009` `VERSION_NOT_SUPPORTED`, `supportedVersions: "1.0"` |

## Review fixes (CR-001, CR-002)

- **Token lifetime (CR-001), verified live:** the JWT returned by `POST /v1/local-auth/login` decodes to `exp - iat = 86400` seconds (24 hours).
- **Credential styles (CR-001), not verified live:** bearer, basic, API key and AWS signature are read from `SettingsService.get_a2a_creds` in source; the demo agents have no authentication, so only the no-credentials path ran. `a2a-integration.md` marks this as not exercised.
- **UI click path (CR-001), not verified:** not run in this verification (UI not driven). The doc now says so and points to the REST sequence that was run live (`fetch`, `POST /v1/assistants`, chat).
- **Wrapper guide snippets (CR-002):** executor and AgentCard code removed; the guide now describes the mapping and links to the agent source files instead of reproducing code. Wire captures in the guide are from sections 1.2 and 1.4 of this report.
