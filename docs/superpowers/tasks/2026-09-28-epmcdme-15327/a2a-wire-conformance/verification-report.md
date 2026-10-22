# Verification report — a2a-wire-conformance (EPMCDME-15327 slice 5)

Date: 2026-09-30. Code under test: HEAD `7a204a7f0` (branch `EPMCDME-15327_upgrade-a2a-protocol`).
Environment: WSL Ubuntu, Docker (inside WSL), Postgres 17 container `codemie-dev-postgres-1` (pre-existing), CodeMie app run with `uvicorn` in WSL (`IDP_PROVIDER=local`, `ENABLE_USER_MANAGEMENT=false`) against a throwaway DB `a2a_verify` (dropped afterwards). Demo agents are used only for communication testing; nothing was copied into the repo. No file under `src/` or `tests/` was modified by this verification.

## Summary

| Result | Count |
|---|---|
| PASS | 34 |
| FAIL | 4 (all one root cause, BUG-1) |
| BLOCKED | 0 |

**BUG-1 (blocking): every response that serializes a `Task` loaded from the database fails with `-32603`.** Non-streaming `SendMessage`, `GetTask` and a successful `CancelTask` are therefore broken on a real database. The streaming path works because it serializes in-memory objects. Unit tests did not catch it because they build `Task` objects in memory or mock the DB. Introduced in `7a204a7f0` (CR-004 fix).

Other findings (non-blocking): see "Other observations".

## 1. Alembic `c9d8e7f6a5b4` on real Postgres — PASS (5/5)

Scratch DB `a2a_verify` created empty, schema `codemie`. Commands (WSL, repo root, `POSTGRES_DB=a2a_verify POSTGRES_HOST=localhost`):
`poetry run alembic -c src/external/alembic/alembic.ini heads | upgrade head | current | downgrade -1 | upgrade head`.

| Check | Result | Evidence |
|---|---|---|
| Single head | PASS | `c9d8e7f6a5b4 (head)` |
| `upgrade head` from empty | PASS | exit 0; `current` = `c9d8e7f6a5b4` |
| Columns after upgrade | PASS | `a2a_tasks`: `artifacts, assistant_id, contextId, date, history, id, sessionId, status, update_date, user_id` |
| `downgrade -1` | PASS | `current` = `u2v3w4x5y6a7`; columns: `artifacts, date, history, id, sessionId, status, update_date`; only index `a2a_tasks_pkey` |
| Re-`upgrade head` | PASS | `current` = `c9d8e7f6a5b4`; `assistant_id`/`contextId`/`user_id` back, indexes recreated (the migration's `assistant_id`/`user_id` indexes) |

## 2. Demo agents started in Docker (WSL) — PASS (4/4)

Images built with `docker build --build-context ai-run-agents=<python dir> <agent dir>`; run with `--network host --env-file <scratch demo.env>` (Azure OpenAI credentials taken from the CodeMie `.env`; never printed).

| Agent | Port | Card | Result |
|---|---|---|---|
| text-insights-agent (official a2a-sdk 1.1.5) | 10800 | `/.well-known/agent-card.json` 200 | PASS |
| howto-guide-agent (official a2a-sdk 1.1.5) | 10900 | `/.well-known/agent-card.json` 200 | PASS |
| currency_converter (legacy hand-rolled) | 10000 | `agent-card.json` 404, `agent.json` 200 | PASS (legacy) |
| research-agent (legacy hand-rolled) | 10700 | `agent-card.json` 404, `agent.json` 200 | PASS (legacy) |

## 3. CodeMie client -> official-SDK agents — PASS (8/8)

Script: `client_verify.py` (scratchpad), run under the codemie poetry env. Uses `A2ACardResolver`, `A2AClient`, `RemoteAgentConnections`; wire captured through an httpx hook.

- **text-insights (non-streaming)** — PASS
  - card fetch OK: `supportedInterfaces[{url:"http://0.0.0.0:10800/", protocolBinding:"JSONRPC", protocolVersion:"0.3"}]` (the SDK 1.1.5 agent emits a v0.3-flavoured card: both v1.0 `supportedInterfaces` and legacy `url`/`preferredTransport`/`protocolVersion:"0.3"`); CodeMie parsed it.
  - Request sent (header `A2A-Version: 1.0`): `{"jsonrpc":"2.0","id":..,"method":"SendMessage","params":{"message":{"messageId":..,"contextId":..,"role":"ROLE_USER","parts":[{"text":"Customer says: ..."}]}}}`
  - Response: HTTP 200 `{"result":{"message":{"messageId":..,"role":"ROLE_AGENT","parts":[{"text":"Status: completed\nSentiment: Negative\n..."}]}}}` — the `{message}` variant of `SendMessageResponse`, parsed by `A2AClient.send_task` (`client_outcome: OK`).
  - `RemoteAgentConnections.send_task`: callback received `Message`, returned `Message` — PASS.
- **howto-guide (streaming)** — PASS
  - `SendStreamingMessage` request as above; HTTP 200 `text/event-stream`.
  - 9 frames, kinds `task, artifactUpdate x7, statusUpdate`; stream terminated on the final status; `client_outcome: OK`.
  - `RemoteAgentConnections.send_task`: callbacks `Task, Task, TaskArtifactUpdateEvent x8, TaskStatusUpdateEvent` — streaming completion handled.
- Card-level `A2A-Version: 1.0` header present on card GET and POST — PASS.
- `protocolVersion "0.3"` inside the remote interface did not cause rejection (accepted as-is) — note below (OBS-3).

## 4. CodeMie client -> legacy agents — PASS (2/2)

`currency_converter` and `research-agent`: card fetch of `agent-card.json` -> `HTTP 404` (`"Error fetching agent card: HTTP 404"`, clean failure, no crash). Direct `A2AClient(url=...)` `SendMessage` -> remote answered HTTP 400 `{"error":{"code":-32600,"message":"Request payload validation error","data":[{"type":"union_tag_invalid",... "expected 'tasks/send', ..."}]}}` -> CodeMie raised `A2AProtocolVersionError: Remote agent does not support A2A protocol 1.0`. Clear, typed, no 500/garbage.

## 5. Official-SDK client (a2a-sdk 1.1.5) -> CodeMie server

Assistants: `00ca83c5-d63b-4ffb-bcda-7b5c57d83937` (private, owner `a2a-verify-user`) and `2529dd6d-1ca2-4450-8619-14505e35c1b3` (shared, project `demo`). Model `gpt-4.1`; the real LLM works (answers recorded below). SDK script `sdk_client.py` run inside the demo image.

| # | Check | Result | Evidence |
|---|---|---|---|
| 5.1 | SDK `A2ACardResolver` parses CodeMie's card (strict protobuf) | PASS | `supportedInterfaces[{…, protocolBinding:"JSONRPC", protocolVersion:"1.0"}]`, `securitySchemes`, `capabilities{streaming:true, pushNotifications:false, extendedAgentCard:true}` |
| 5.2 | SDK `SendMessage` streaming=True | PASS | kinds `task, statusUpdate, artifactUpdate, statusUpdate`; final `TASK_STATE_COMPLETED`; artifact text `"Hello!"` |
| 5.3 | SDK `GetExtendedAgentCard` | PASS | full card via JSON-RPC |
| 5.4 | SDK `CancelTask` on completed task | PASS | `-32002 TaskNotCancelableError` (my script labelled it FAIL by expectation; behaviour is correct) |
| 5.5 | SDK `SendMessage` streaming=False | **FAIL (BUG-1)** | `A2AClientError: HTTP Error 400` |
| 5.6 | SDK `GetTask` | **FAIL (BUG-1)** | `A2AClientError: HTTP Error 400` |

Raw wire (curl, header `A2A-Version: 1.0` unless noted; full output in scratchpad `raw_checks.out`, `raw_stream.out`, `owner.out`):

| # | Check | Expected | Actual | Result |
|---|---|---|---|---|
| R1 | private card, anonymous | 401 | 401 `Authentication failed` | PASS |
| R2 | private card, owner | 200 card | 200; no internal fields (`project_name`, `integration_id`, `user_id`, `bedrock_agentcore`) | PASS |
| R3 | private card, other user | 403 | 403 `Access Denied` | PASS |
| R4 | no `A2A-Version` | -32009 | -32009 + `google.rpc.ErrorInfo{reason:"VERSION_NOT_SUPPORTED",metadata:{supportedVersions:"1.0"}}`, HTTP 400 | PASS |
| R5 | `A2A-Version: 0.3` | -32009 | -32009 | PASS |
| R6 | version via `?A2A-Version=1.0`, unknown task | -32001 | -32001 `TASK_NOT_FOUND`, HTTP 200 | PASS |
| R7 | `message/send` | -32601 | -32601 `METHOD_NOT_FOUND`, HTTP 400 | PASS |
| R8 | `ListTasks` | -32601 | -32601 | PASS |
| R9 | `SubscribeToTask` | -32601 | -32601 | PASS |
| R10 | non-JSON body | -32700 | -32700 `JSON_PARSE_ERROR` | PASS |
| R11 | `SendMessage` with empty `parts` | -32602 | -32602 + `google.rpc.BadRequest.fieldViolations[message.parts]` | PASS |
| R12 | `{"hello":"world"}` | -32600 | -32600 "Invalid JSON-RPC 2.0 request. Supported A2A protocol version is 1.0." | PASS |
| R13 | `GetExtendedAgentCard` | card | full card, same shape as R2 | PASS |
| R14 | `returnImmediately:true` | -32004 | -32004 | PASS |
| R15 | `acceptedOutputModes:["image/png"]` | -32005 | -32005 | PASS |
| R16 | `SendMessage` real | `{task:{…}}` | **-32603, HTTP 400** (task actually ran: stored as `COMPLETED`, artifact "Hello, nice to meet you!") | **FAIL (BUG-1)** |
| S1 | `SendStreamingMessage` raw | SSE frames | `{"task"}` (SUBMITTED) -> `{"statusUpdate"}` WORKING -> `{"artifactUpdate"}` -> `{"statusUpdate"}` COMPLETED; no `final`, no `kind`/`type` | PASS |
| S2 | `GetTask` of a completed task | task | **-32603, HTTP 400** | **FAIL (BUG-1)** |
| S3 | `CancelTask` completed | -32002 | -32002 `TASK_NOT_CANCELABLE`, HTTP 200 | PASS |
| O1 | other user reads shared card | 200 | 200 | PASS |
| O3 | other user `GetTask` on owner's task | -32001 | -32001 | PASS |
| O4 | other user `CancelTask` | -32001 | -32001 | PASS |
| O5 | other user continues owner's task via `message.taskId` | -32001 | -32001 | PASS |
| O6 | owner sends to a completed task | -32004 | -32004 `UNSUPPORTED_OPERATION` | PASS |

## Bugs found

### BUG-1 (blocking) — DB-loaded `Task` cannot be serialized: `PydanticSerializationError` -> `-32603`

- **Repro:** run the app against Postgres, create any assistant, then
  `POST /v1/a2a/assistants/<id>` with `{"jsonrpc":"2.0","id":1,"method":"SendMessage","params":{"message":{"messageId":"m2","role":"ROLE_USER","parts":[{"text":"Say hello"}]}}}` and `A2A-Version: 1.0`.
  Then `GetTask` with the returned/stored task id.
- **Expected:** `{"result":{"task":{…}}}` / `{"result":{…task…}}`. **Actual:** HTTP 400 `-32603 Internal error`; app log: `Unhandled exception: Error calling function '_serialize_wire': TypeError: 'NoneType' object is not subscriptable`.
- **Root cause (verified):** `src/codemie/rest_api/a2a/types.py:186` declares `_wire_metadata = PrivateAttr(default=None)` on the SQLModel table model `Task`; `_serialize_wire` (`:204-217`) reads `self._wire_metadata` (`:210`). Instances materialised by SQLAlchemy from a DB row never run pydantic `__init__`, so `__pydantic_private__` is `None` (measured: in-memory `Task` -> `{'_wire_metadata': None}`; DB-loaded `Task` -> `None`) and the attribute access raises. Seeding `row.__pydantic_private__ = {"_wire_metadata": None}` makes the dump succeed and produce the correct wire JSON (so the fix direction is confirmed).
- **Affected paths:** non-streaming `SendMessage`, `GetTask`, successful `CancelTask`, and anything else returning `Task.find_by_id(...)` results. Not affected: streaming (serializes in-memory objects), `CancelTask` on a terminal task, error paths.
- **Why tests missed it:** they construct `Task` in memory or mock `Task.find_by_id`; no test serializes a DB-materialised row.
- **Introduced by:** `7a204a7f0` (CR-004 fix, added `_wire_metadata`).
- **Suggested fix (not applied):** make the read tolerant of the uninitialised private state (e.g. a `getattr`/`__pydantic_private__ or {}` guard in `wire_metadata` and `_serialize_wire`), or keep wire metadata as a normal excluded field instead of a private attribute; add a test that round-trips a Task through an actual SQLAlchemy session (or builds one via `Task.__new__`/`sqlmodel` ORM instantiation).

## Other observations (non-blocking)

- **OBS-1 (interop, medium):** CodeMie answers pre-dispatch JSON-RPC errors (-32009, -32601, -32600, -32602, -32603, -32700) with HTTP 400. The official SDK client surfaces these as a generic `A2AClientError: HTTP Error 400`, dropping the JSON-RPC error code/detail (seen at 5.5/5.6, where the error was -32603). The spec's JSON-RPC binding defines no HTTP mapping for errors; returning HTTP 200 with the JSON-RPC error body (as CodeMie already does for task-level errors) would let SDK clients see the typed error. CodeMie's own client reads the body on 4xx, so this only affects external clients.
- **OBS-2 (card strictness, low):** `provider` is emitted as `{"organization":"","url":""}` and skills carry `tags: []` (proto marks both as required / non-empty arrays). The SDK 1.1.5 parser accepted the card (shown as `provider: {}`), so this is informational.
- **OBS-3 (client, low):** the client does not inspect a remote card's interface `protocolVersion`. The SDK demo agents advertise `"0.3"` in `supportedInterfaces[0].protocolVersion` yet accept v1.0 `SendMessage`, so a pre-check would have produced false rejections; the `A2AProtocolVersionError` on the actual call is the right mechanism. No action suggested.
- **OBS-4 (workspace):** `tests/codemie/rest_api/a2a/fixtures/__init__.py` is modified in the working tree (license header added at 19:40 during the QA-gates `make license-check` run, before this verification). It is uncommitted; the slice commits do not contain it, so it should be committed or reverted deliberately (the next commit will otherwise miss the header).

## Commands used (abridged)

```
# Alembic (WSL)
docker exec codemie-dev-postgres-1 createdb -U postgres a2a_verify
POSTGRES_DB=a2a_verify POSTGRES_HOST=localhost poetry run alembic -c src/external/alembic/alembic.ini upgrade head | downgrade -1 | upgrade head | current
# Demo agents (WSL)
docker build --build-context ai-run-agents=<demo>/python -t a2averify-<agent> <demo>/python/agents/<agent>
docker run -d --name a2av-<x> --network host --env-file <scratch>/demo.env a2averify-<agent> [uv run . --host 0.0.0.0 --port <p>]
# CodeMie client -> agents
poetry run python <scratch>/client_verify.py
# App (WSL)
POSTGRES_DB=a2a_verify POSTGRES_HOST=localhost IDP_PROVIDER=local ENABLE_USER_MANAGEMENT=false \
  poetry run python -m uvicorn codemie.rest_api.main:app --app-dir src --host 0.0.0.0 --port 8080
# Assistants via POST /v1/assistants with header user-id (local IDP)
# SDK client -> CodeMie
docker run --rm --network host -v <scratch>:/w a2averify-text-insights-agent python /w/sdk_client.py <base-url> a2a-verify-user
# raw checks: curl scripts raw_checks.sh, raw_stream.sh, owner.sh (scratchpad)
# BUG-1 repro: poetry run python <scratch>/repro2.py (POSTGRES_DB=a2a_verify)
```

Cleanup: app stopped; containers `a2av-ti/-hg/-cc/-ra` and images `a2averify-*` removed; scratch DB `a2a_verify` dropped; pre-existing `codemie-dev-postgres-1` and `codemie-dev-elasticsearch-1` left running. No commits, no pushes.

---

# Re-verification at c52a66523

Code under test: HEAD `c52a66523` (`644177b70` fixes BUG-1; `c52a66523` returns all JSON-RPC errors with HTTP 200). Same environment and rules as above: app run with `uvicorn` in WSL against a fresh scratch DB `a2a_verify` migrated with `alembic upgrade head` (setup only; Alembic itself was not re-verified, per instruction), real LLM (`gpt-4.1`), demo agents in Docker (WSL), official `a2a-sdk` 1.1.5 client. No file under `src/` or `tests/` modified; no commits/pushes. Everything started was removed afterwards (app, `a2av-*` containers and `a2averify-*` images, scratch DB); `codemie-dev-postgres-1` and `codemie-dev-elasticsearch-1` left running. Assistant used: `58b89adf-f265-4c73-9ece-2dc9c796a571` (private, owner `a2a-verify-user`).

## Verdict on earlier findings

| Finding | Status at c52a66523 |
|---|---|
| **BUG-1** DB-loaded `Task` fails to serialize (-32603) | **RESOLVED** — non-streaming `SendMessage`, `GetTask` and `CancelTask` now return tasks from real DB rows (checks A1-A5) |
| **OBS-1** pre-dispatch JSON-RPC errors answered with HTTP 400, SDK shows generic HTTP error | **RESOLVED** — all JSON-RPC errors are HTTP 200; the SDK client raises the typed error (checks B1-B8) |

## (a) Checks that failed because of BUG-1 — PASS (5/5)

| # | Check | Result | Evidence |
|---|---|---|---|
| A1 | Non-streaming `SendMessage` (raw curl, real LLM run) | PASS | HTTP 200 `{"result":{"task":{"id":"dc87…","contextId":"ctx-rv-1","status":{"state":"TASK_STATE_COMPLETED",…},"artifacts":[{"artifactId":"058b…","parts":[{"text":"Hello, hope you have a great day!"}]}],"history":[…ROLE_USER…]}}}` |
| A2 | Non-streaming `SendMessage` via official SDK (`streaming=False`) | PASS | `kinds=['task'] state=TASK_STATE_COMPLETED text='Hello, nice to meet you!'`, artifact present |
| A3 | `GetTask` (raw, `historyLength` 5 and 0; and via SDK `history_length=5`) | PASS | HTTP 200; task returned from the DB row; `historyLength:0` omits `history`; SDK parsed the full `Task` |
| A4 | `CancelTask` success on a DB-loaded non-terminal task | PASS | task row `rv-working-1` (`WORKING`, owned) -> `CancelTask` = HTTP 200 `{"result":{"id":"rv-working-1","status":{"state":"TASK_STATE_CANCELED",…}}}`; `GetTask` afterwards still `CANCELED`; second `CancelTask` -> `-32002 TASK_NOT_CANCELABLE` (HTTP 200) |
| A5 | Legacy rows against real DB | PASS | NULL-owner row -> `-32001`; owned row with old shapes (`"state":"input-required"`, `"role":"user"`, `{"type":"text"}`, `{"type":"file","file":{bytes,mimeType,name}}`, artifact `index`) read and normalised to `TASK_STATE_INPUT_REQUIRED`, `ROLE_USER`, `{"text":..}`, `{"raw":"aGk=","filename":"a.txt","mediaType":"text/plain"}`, generated `artifactId`/`messageId`, `contextId` from `sessionId` |

(A4 uses a directly inserted `WORKING` row because a task cannot be cancelled while it runs — see OBS-5.)

## (b) Pre-dispatch errors, now HTTP 200 and typed in the SDK — PASS (8/8)

| # | Check | Result | Raw (curl) | Official SDK client |
|---|---|---|---|---|
| B1 | missing `A2A-Version` | PASS | HTTP 200 `-32009` + `ErrorInfo{reason:VERSION_NOT_SUPPORTED, supportedVersions:"1.0"}` | `VersionNotSupportedError` (via `A2A-Version: 0.3` override) |
| B2 | `message/send` | PASS | HTTP 200 `-32601` | `MethodNotFoundError` (SDK `list_tasks`, also out of scope) |
| B3 | bad params (empty `parts`) | PASS | HTTP 200 `-32602` + `BadRequest.fieldViolations[message.parts]` | `InvalidParamsError` |
| B4 | non-JSON body | PASS | HTTP 200 `-32700` | n/a (SDK cannot send invalid JSON) |
| B5 | not a JSON-RPC request | PASS | HTTP 200 `-32600` | n/a |
| B6 | unknown task | PASS | HTTP 200 `-32001` | `TaskNotFoundError` |
| B7 | cancel completed task | PASS | HTTP 200 `-32002` | `TaskNotCancelableError` |
| B8 | auth/authz unchanged (still plain HTTP errors, not JSON-RPC) | PASS | anonymous POST -> 401; other user on private assistant -> 403 | — |

## (c) CodeMie client smoke — PASS (3/3, no regression)

| # | Check | Result | Evidence |
|---|---|---|---|
| C1 | CodeMie client -> `text-insights` (non-streaming) | PASS | `client_outcome: OK`; `RemoteAgentConnections` callback `Message`, returned `Message`; header `A2A-Version: 1.0` on card GET and POST |
| C2 | CodeMie client -> `howto-guide` (streaming) | PASS | 11 frames, kinds `task, artifactUpdate x9, statusUpdate`; `client_outcome: OK`; stream ended on the final status |
| C3 | CodeMie client -> `currency_converter` (legacy) | PASS | card `agent-card.json` 404 handled; direct `SendMessage` -> `A2AProtocolVersionError: Remote agent does not support A2A protocol 1.0` |

Totals for the re-verification: **21 PASS, 0 FAIL, 0 BLOCKED**.

## New observations (non-blocking)

- **OBS-5 (medium) — the agent call blocks the server's event loop.** `invoke_with_a2a_output` is a synchronous LLM call executed inside the async request handler/generator. Measured: (1) for a streaming request the `task` and `WORKING` frames reached the client only when the run finished (stream file was 0 bytes at +1.3 s, four frames arrived together ~7 s later), so streaming is not incremental in practice; (2) a `CancelTask` sent 1.3 s into a running task waited 6.3 s for the loop and then returned `-32002` because the task had already completed — cancel of a running task can never take effect (accepted gap "cancel only flips stored status" is stronger: the cancel is not even processed until the run ends). Suspected location: `src/codemie/rest_api/a2a/server/codemie_task_manager.py` (`_invoke_agent` / `_handle_a2a_stream` call `agent.invoke_with_a2a_output(...)` synchronously). Pre-existing pattern for `SendMessage` (was already synchronous before slice 5); new in the sense that slice 2 advertises `streaming:true`. A fix would run the agent in a worker thread (`run_in_threadpool`/`asyncio.to_thread`). Not a wire-conformance defect.
- **OBS-6 (info):** the SDK client exposes `history_length` (snake_case, protobuf field names) in Python; on the wire it is `historyLength` — CodeMie handled the SDK's ProtoJSON correctly.

Commands added for this round (scratchpad scripts): `rv_raw.sh` (curl checks A1/A3/B1-B8), `rv_cancel.sh` (running-task cancel timing), `rv_cancel2.sh` (A4/A5 DB rows via `psql insert`), `sdk_client2.py` run in the `a2averify-text-insights-agent` image (A2/A3/B1-B3/B6/B7), `client_verify.py text-insights-agent howto-guide-agent currency_converter` (C1-C3).
